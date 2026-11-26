#!/usr/bin/env python3

from datetime import datetime, timezone
import os
import subprocess
import sys

from harness import TestContext, requires, run
from harness.results import test_fail

@requires(features={'git'}, transports={'pipe'}, mutates={'filesystem', 'process'})
def test(context: TestContext):
    repository = context.scratch / 'repository'
    repository.mkdir()
    tracked = repository / 'tracked'
    dirty = repository / 'dirty'
    tracked.write_text('tracked\n')
    dirty.write_text('original\n')
    subprocess.run(['git', 'init', '-q'], cwd=repository, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Rsync test'], cwd=repository, check=True)
    subprocess.run(['git', 'config', 'user.email', 'rsync-test@example.invalid'], cwd=repository, check=True)
    subprocess.run(['git', 'add', '.'], cwd=repository, check=True)
    committed = datetime(2001, 2, 3, 17, 17, 42, tzinfo=timezone.utc)
    env = {**os.environ, 'GIT_AUTHOR_DATE': committed.isoformat(),
           'GIT_COMMITTER_DATE': committed.isoformat()}
    subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=repository, env=env, check=True)
    dirty.write_text('modified\n')
    current = 1_700_000_000
    os.utime(tracked, (current, current))
    os.utime(dirty, (current, current))

    script = context.repository / 'support' / 'git-set-file-times'
    listed = subprocess.run([sys.executable, str(script), '--list'], cwd=repository,
                            capture_output=True, text=True)
    if listed.returncode or '* 2001-02-03 17:17:42 tracked' not in listed.stdout:
        test_fail(f'git-set-file-times listed the wrong timestamp:\n{listed.stdout}{listed.stderr}')
    result = subprocess.run([sys.executable, str(script), '--quiet'], cwd=repository,
                            capture_output=True, text=True)
    if result.returncode:
        test_fail(f'git-set-file-times failed:\n{result.stdout}{result.stderr}')
    if int(tracked.stat().st_mtime) != int(committed.timestamp()):
        test_fail('git-set-file-times did not apply the commit time')
    if int(dirty.stat().st_mtime) != current:
        test_fail('git-set-file-times changed a modified file')

if __name__ == '__main__':
    run(test)
