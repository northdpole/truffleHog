#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
import shutil
import sys
import math
import datetime
import argparse
import uuid
import tempfile
import os
import re
import json
import stat
from pprint import pprint

from git import Repo, RemoteProgress
from git import Repo
from git import NULL_TREE
from regexChecks import regexes


def main():
    parser = argparse.ArgumentParser(description='Find secrets hidden in the depths of git.')
    parser.add_argument('--json', dest="output_json", action="store_true", help="Output in JSON")
    parser.add_argument("--regex", dest="do_regex", action="store_true", help="Enable high signal regex checks")
    parser.add_argument("--rules", dest="rules", help="Ignore default regexes and source from json list file")
    parser.add_argument("--entropy", dest="do_entropy", help="Enable entropy checks")
    parser.add_argument("--since_commit", dest="since_commit", help="Only scan from a given commit hash")
    parser.add_argument("--max_depth", dest="max_depth",
                        help="The max commit depth to go back when searching for secrets")
    parser.add_argument('git_url', type=str, help='URL for secret searching')
    parser.set_defaults(regex=False)
    parser.set_defaults(rules={})
    parser.set_defaults(max_depth=1000000)
    parser.set_defaults(since_commit=None)
    parser.set_defaults(entropy=True)
    args = parser.parse_args()
    rules = {}
    if args.rules:
        try:
            with open(args.rules, "r") as ruleFile:
                rules = json.loads(ruleFile.read())
                for rule in rules:
                    rules[rule] = re.compile(rules[rule])
        except (IOError, ValueError) as e:
            raise ("Error reading rules file")
        for regex in dict(regexes):
            del regexes[regex]
        for regex in rules:
            regexes[regex] = rules[regex]
    do_entropy = str2bool(args.do_entropy)
    output = find_strings(args.git_url, args.since_commit, args.max_depth, args.output_json, args.do_regex, do_entropy,do_print=True)
    project_path = output["project_path"]
    shutil.rmtree(project_path, onerror=del_rw)


def str2bool(v):
    if v == None:
        return True
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
HEX_CHARS = "1234567890abcdefABCDEF"


def del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    os.remove(name)


def shannon_entropy(data, iterator):
    """
    Borrowed from http://blog.dkbza.org/2007/05/scanning-data-for-entropy-anomalies.html
    """
    if not data:
        return 0
    entropy = 0
    for x in iterator:
        p_x = float(data.count(x)) / len(data)
        if p_x > 0:
            entropy += - p_x * math.log(p_x, 2)
    return entropy


def get_strings_of_set(word, char_set, threshold=20):
    count = 0
    letters = ""
    strings = []
    for char in word:
        if char in char_set:
            letters += char
            count += 1
        else:
            if count > threshold:
                strings.append(letters)
            letters = ""
            count = 0
    if count > threshold:
        strings.append(letters)
    return strings


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def clone_git_repo(git_url, progress=None):
    project_path = tempfile.mkdtemp()
    Repo.clone_from(git_url, project_path, progress=progress)
    return project_path


def print_results(printJson, issue):
    commit_time = issue['date']
    branch_name = issue['branch']
    prev_commit = issue['commit']
    printableDiff = issue['printDiff']
    commitHash = issue['commitHash']
    reason = issue['reason']
    path = issue['path']

    if printJson:
        print(json.dumps(issue, sort_keys=True, indent=4))
    else:
        print("~~~~~~~~~~~~~~~~~~~~~")
        reason = "{}Reason: {}{}".format(bcolors.OKGREEN, reason, bcolors.ENDC)
        print(reason)
        dateStr = "{}Date: {}{}".format(bcolors.OKGREEN, commit_time, bcolors.ENDC)
        print(dateStr)
        hashStr = "{}Hash: {}{}".format(bcolors.OKGREEN, commitHash, bcolors.ENDC)
        print(hashStr)
        filePath = "{}Filepath: {}{}".format(bcolors.OKGREEN, path, bcolors.ENDC)
        print(filePath)

        if sys.version_info >= (3, 0):
            branchStr = "{}Branch: {}{}".format(bcolors.OKGREEN, branch_name, bcolors.ENDC)
            print(branchStr)
            commitStr = "{}Commit: {}{}".format(bcolors.OKGREEN, prev_commit, bcolors.ENDC)
            print(commitStr)
            print(printableDiff)
        else:
            branchStr = "{}Branch: {}{}".format(bcolors.OKGREEN, branch_name.encode('utf-8'), bcolors.ENDC)
            print(branchStr)
            commitStr = "{}Commit: {}{}".format(bcolors.OKGREEN, prev_commit.encode('utf-8'), bcolors.ENDC)
            print(commitStr)
            print(printableDiff.encode('utf-8'))
        print("~~~~~~~~~~~~~~~~~~~~~")


def find_entropy(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash):
    stringsFound = []
    lines = printableDiff.split("\n")
    for line in lines:
        for word in line.split():
            base64_strings = get_strings_of_set(word, BASE64_CHARS)
            hex_strings = get_strings_of_set(word, HEX_CHARS)
            for string in base64_strings:
                b64Entropy = shannon_entropy(string, BASE64_CHARS)
                if b64Entropy > 4.5:
                    stringsFound.append(string)
                    printableDiff = printableDiff.replace(string, bcolors.WARNING + string + bcolors.ENDC)
            for string in hex_strings:
                hexEntropy = shannon_entropy(string, HEX_CHARS)
                if hexEntropy > 3:
                    stringsFound.append(string)
                    printableDiff = printableDiff.replace(string, bcolors.WARNING + string + bcolors.ENDC)
    entropicDiff = None
    if len(stringsFound) > 0:
        # print("found ",len(stringsFound)," entropic issues")
        entropicDiff = {}
        entropicDiff['date'] = commit_time
        entropicDiff['path'] = blob.b_path if blob.b_path else blob.a_path
        entropicDiff['branch'] = branch_name
        entropicDiff['commit'] = prev_commit.message
        entropicDiff['diff'] = blob.diff.decode('utf-8', errors='replace')
        entropicDiff['stringsFound'] = stringsFound
        entropicDiff['printDiff'] = printableDiff
        entropicDiff['commitHash'] = commitHash
        entropicDiff['reason'] = "High Entropy"
    return entropicDiff


def regex_check(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash, custom_regexes=None):
    if custom_regexes:
        secret_regexes = custom_regexes
    else:
        secret_regexes = regexes
    regex_matches = []
    for key in secret_regexes:
        found_strings = secret_regexes[key].findall(printableDiff)
        for found_string in found_strings:
            found_diff = printableDiff.replace(printableDiff, bcolors.WARNING + found_string + bcolors.ENDC)
        if found_strings:
            # print("found ", len(found_strings), " regex issues")
            foundRegex = {}
            foundRegex['date'] = commit_time
            foundRegex['path'] = blob.b_path if blob.b_path else blob.a_path
            foundRegex['branch'] = branch_name
            foundRegex['commit'] = prev_commit.message
            foundRegex['diff'] = blob.diff.decode('utf-8', errors='replace')
            foundRegex['stringsFound'] = found_strings
            foundRegex['printDiff'] = found_diff
            foundRegex['reason'] = key
            foundRegex['commitHash'] = commitHash
            regex_matches.append(foundRegex)
    return regex_matches


def diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes, do_entropy, do_regex,
                printJson, do_print=False):
    issues = {"entropicDiffs": [], "found_regexes": []}
    for blob in diff:
        printable_diff = blob.diff.decode('utf-8', errors='replace')
        if printable_diff.startswith("Binary files"):
            continue
        commit_time = datetime.datetime.fromtimestamp(prev_commit.committed_date).strftime('%Y-%m-%d %H:%M:%S')
        found_issues = []
        if do_entropy:
            entropic_diff = find_entropy(printable_diff, commit_time, branch_name, prev_commit, blob, commitHash)
            if entropic_diff:
                issues['entropicDiffs'].extend([entropic_diff])
        if do_regex:
            found_regexes = regex_check(printable_diff, commit_time, branch_name, prev_commit, blob, commitHash,
                                        custom_regexes)
            issues["found_regexes"].extend(found_regexes)
        if do_print:
            for issue_list in issues.values():
                for issue in issue_list:
                    print_results(printJson, issue)
    return issues


class MyProgressPrinter(RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=''):
        print(op_code, cur_count, max_count, cur_count / (max_count or 100.0), message or "NO MESSAGE")


def handle_results(output, output_dir, found_issues):
    # TODO this needs to extend itself with returning the actual results of the the found issues
    for foundIssue in found_issues:
        result_path = os.path.join(output_dir, str(uuid.uuid4()))
        with open(result_path, "w+") as result_file:
            result_file.write(json.dumps(foundIssue))
        output["foundIssues"].append(result_path)
    return output


def find_strings(git_url, since_commit=None, max_depth=None, printJson=False, do_regex=False, do_entropy=True,
                 specific_branch=None, print_str=3, custom_regexes={}, results_files=False):
    output = {"entropicDiffs": [], "found_regexes": []}
    project_path = clone_git_repo(git_url)
    repo = Repo(project_path)
    already_searched = set()
    output_dir = tempfile.mkdtemp()
    do_print = False
    branches = repo.remotes.origin.fetch()
    for remote_branch in branches:
        since_commit_reached = False
        branch_name = remote_branch.name.split('/')[1]
        if specific_branch is not None and branch_name != specific_branch:
            pprint("Specific branch is %s, current branch is %s continuing" % (specific_branch, branch_name))
            continue
        try:
            print("checking out ", branch_name)
            repo.git.checkout(remote_branch)
        except Exception as e:
            print("Checkout failed because of %s" % e)
            pass

        prev_commit = None
        commits = repo.iter_commits(max_count=max_depth)
        if print_str >= 3:
            do_print=True
            print("checking ", git_url, " with ", str(len(list(commits))), " commits")

        for curr_commit in commits:
            commitHash = curr_commit.hexsha
            if commitHash == since_commit:
                since_commit_reached = True
            if since_commit and since_commit_reached:
                prev_commit = curr_commit
                continue
            # if not prev_commit, then curr_commit is the newest commit. And we have nothing to diff with.
            # But we will diff the first commit with NULL_TREE here to check the oldest code.
            # In this way, no commit will be missed.
            if not prev_commit:
                prev_commit = curr_commit
                continue
            else:
                # avoid searching the same diffs
                hashes = str(prev_commit) + str(curr_commit)
                if hashes in already_searched:
                    prev_commit = curr_commit
                    continue
                already_searched.add(hashes)

                diff = prev_commit.diff(curr_commit, create_patch=True)
            found_issues = diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes,
                                      do_entropy, do_regex, printJson,do_print=do_print)

            output['found_regexes'].extend(found_issues['found_regexes'])
            output['entropicDiffs'].extend(found_issues['entropicDiffs'])
            if results_files:
                output = handle_results(output, output_dir, found_issues)
            prev_commit = curr_commit
        # Handling the first commit
        diff = curr_commit.diff(NULL_TREE, create_patch=True)
        found_issues = diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes, do_entropy,
                                  do_regex, printJson,do_print=do_print)
        if results_files:
            output = handle_results(output, output_dir, found_issues)
    output["project_path"] = project_path
    output["clone_uri"] = git_url
    return output


if __name__ == "__main__":
    main()
