#!/usr/bin/env python3

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from harness import TestContext, requires, run, runner
from harness.rsync import rsync_command_binary, test_fail

@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    recipe = context.repository / 'testsuite' / 'tools' / 'check.sh'
    if not recipe.is_file():
        test_fail('shared check recipe is missing')
    binary = rsync_command_binary()
    output = context.scratch / 'native run'
    command = ['sh', str(recipe), str(context.tools), binary, 'pipe,self-peer', str(output)]
    env = os.environ.copy()
    env['whichtests'] = 'must-not-run'

    def invoke(args, success, message=None):
        result = subprocess.run(args, env=env, cwd=context.scratch, capture_output=True, text=True, timeout=60)
        if (result.returncode == 0) != success:
            test_fail(f'wrong recipe result: {result.returncode}\n{result.stdout}{result.stderr}')
        if message and message not in result.stdout + result.stderr:
            test_fail(f'missing recipe diagnostic: {message}\n{result.stdout}{result.stderr}')
        return result

    python_dir = context.scratch / 'python'
    python_dir.mkdir()
    python = python_dir / 'python3'
    python.write_text(f'#!{sys.executable}\nimport os, sys\n'
                      'assert sys.argv[1] == "-S"\n'
                      'os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n')
    python.chmod(0o755)
    env['PATH'] = str(python_dir) + os.pathsep + os.environ['PATH']
    invoke(command + ['cli-transfers', '-j2'], True)
    env['PATH'] = os.environ['PATH']
    receipt = json.loads((output / 'receipt.json').read_text())
    identity = receipt['run']
    digest = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    if (identity['binary_sha256'] != digest or identity['peer_sha256'] != digest
            or identity['euid'] != os.geteuid() or identity['selected'] != ['cli-transfers']
            or receipt['summary']['verdicts'] != {'pass': 1}):
        test_fail('recipe receipt identifies the wrong run')
    if identity['source_revision'] is not None and not isinstance(identity['source_dirty'], bool):
        test_fail('source revision has no working-tree state')
    if (context.repository / '.git').exists() and shutil.which('git'):
        git = ['git', '--no-optional-locks', '-c', f'safe.directory={context.repository.resolve()}',
               '-C', str(context.repository)]
        revision = subprocess.run(git + ['rev-parse', 'HEAD'], capture_output=True, text=True, timeout=30)
        status = subprocess.run(git + ['status', '--porcelain'], capture_output=True, text=True, timeout=30)
        expected = (None, None)
        if not revision.returncode and not status.returncode:
            expected = revision.stdout.strip(), bool(status.stdout)
        if (identity['source_revision'], identity['source_dirty']) != expected:
            test_fail('receipt identifies the wrong source checkout')
    checkout = context.scratch / 'checkout'
    (checkout / '.git').mkdir(parents=True)
    for error in (FileNotFoundError('git'), subprocess.CalledProcessError(1, 'git'),
                  subprocess.TimeoutExpired('git', 30)):
        with patch.object(runner.subprocess, 'run', side_effect=error):
            if runner.source_identity(checkout) != (None, None):
                test_fail('unavailable Git metadata was accepted')
    if not (output / 'run.log').is_file() or not (output / 'scratch' / 'testtmp').is_dir():
        test_fail('recipe did not isolate its log and scratch')
    original = (output / 'receipt.json').read_bytes()
    invoke(command + ['cli-transfers'], False)
    if (output / 'receipt.json').read_bytes() != original:
        test_fail('recipe overwrote an existing result')

    def attempt(name, binary_path=binary, profiles='pipe,self-peer', extra=()):
        return ['sh', str(recipe), str(context.tools), binary_path, profiles,
                str(context.scratch / name), 'cli-transfers', *extra]

    invoke(attempt('missing-binary', str(context.scratch / 'absent')), False, 'Not an executable file:')
    wrong = context.scratch / 'wrong-rsync'
    wrong.write_text('#!/bin/sh\nexit 1\n')
    wrong.chmod(0o755)
    invoke(attempt('wrong-binary', str(wrong)), False, '--version failed')
    invoke(attempt('no-profile', profiles=''), False, 'Usage: check.sh')
    invoke(attempt('empty-profiles', profiles=' , '), False, 'no profiles selected')
    invoke(attempt('no-tests', extra=('--exclude=*',)), False, 'no tests selected')
    invoke(attempt('fixed-inputs', extra=(f'--receipt={context.scratch / "elsewhere.json"}',
                                        f'--rsync-bin={wrong}', '--profiles=')), True)
    fixed = json.loads((context.scratch / 'fixed-inputs' / 'receipt.json').read_text())
    if fixed['run']['binary_sha256'] != digest or fixed['run']['profiles'] != ['pipe', 'self-peer']:
        test_fail('runner arguments changed the recipe inputs')
    invoke(['sh', str(recipe), str(context.tools), binary, 'pipe,self-peer',
            str(context.scratch / 'long-parent' / ('directory' * 15)), 'daemon-unix-socket-atfd'], True)
    if os.geteuid() == 0 and sys.platform == 'linux':
        private = context.scratch / 'private-home'
        build = private / 'build'
        build.mkdir(parents=True)
        shutil.copy2(binary, build / 'rsync')
        for name in ('tls', 'trimslash', 't_unsafe', 't_chmod_secure', 't_secure_relpath',
                     'wildtest', 'getgroups', 'getfsdev', 'config.h', 'shconfig'):
            shutil.copy2(context.tools / name, build / name)
        private.chmod(0o700)
        os.chown(private, 65534, 65534)
        invoke(['sh', str(recipe), str(build), str(build / 'rsync'), 'pipe,self-peer',
                str(private / 'results'), 'partial-no-write'], True)
    blocked = context.scratch / 'not-a-directory'
    blocked.touch()
    invoke(attempt('not-a-directory/result'), False)
    fixture = context.scratch / 'fixture'
    tools = fixture / 'testsuite' / 'tools'
    tests = fixture / 'testsuite' / 'tests'
    tools.mkdir(parents=True)
    tests.mkdir()
    shutil.copyfile(recipe, tools / 'check.sh')
    (fixture / 'testsuite' / 'runtests.py').write_text('raise SystemExit(0)\n')
    invoke(['sh', str(tools / 'check.sh'), str(context.tools), binary, 'pipe,self-peer',
            str(context.scratch / 'missing-receipt')], False, 'Missing test receipt:')
    shutil.copyfile(context.repository / 'testsuite' / 'runtests.py', fixture / 'testsuite' / 'runtests.py')
    (tests / 'first.py').write_text('raise SystemExit(1)\n')
    (tests / 'second.py').write_text('raise SystemExit(0)\n')
    profile = fixture / 'profile.json'
    profile.write_text('{}\n')
    output = context.scratch / 'incomplete'
    result = invoke(['sh', str(tools / 'check.sh'), str(context.tools), binary,
                     str(profile), str(output), '--stop-on-fail'], False)
    receipt = json.loads((output / 'receipt.json').read_text())
    if (result.returncode != 1 or receipt['run']['selected'] != ['first', 'second']
            or len(receipt['tests']) != 1 or not receipt['summary']['mismatches']):
        test_fail('an incomplete run lost its failure evidence')
    if receipt['run']['source_revision'] is not None or receipt['run']['source_dirty'] is not None:
        test_fail('a non-checkout source acquired its parent revision')

if __name__ == '__main__':
    run(test)
