#!/usr/bin/env python3

import os
import re
import shutil
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_argv, rsync_path_arg


COMPLETED_ROW = re.compile(r'\b(\d+):(\d\d):(\d\d) \(xfr#1, (?:ir|to)-chk=(\d+)/\d+\)')


@requires(features={'linux_interpose'}, transports={'pipe'},
          mutates={'filesystem', 'process'}, tags={'progress', 'transfer'})
def test(context: TestContext):
    fixture = context.repository / 'testsuite/fixtures/progress-clock.c'
    library = context.scratch / 'progress-clock.so'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture)],
                           capture_output=True, text=True, timeout=30)
    if build.returncode:
        test_fail(f'cannot build the progress clock: {build.stderr}')
    env = {**os.environ, 'LD_PRELOAD': str(library), 'LC_ALL': 'C'}

    source = context.source
    source.mkdir()
    (source / 'a-new').write_bytes(b'n' * (128 * 1024))
    (source / 'z-unchanged').write_bytes(b'u' * (4 * 1024 * 1024))
    for label, peer_options, remote in (
        ('local', ['--no-inc-recursive'], ''),
        ('push', ['-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}'], 'lh:'),
    ):
        destination = context.scratch / label
        destination.mkdir()
        shutil.copy2(source / 'z-unchanged', destination)
        result = subprocess.run(
            rsync_argv('-rt', '--info=progress2', *peer_options, f'{source}/',
                       f'{remote}{destination}/'),
            env=env, capture_output=True, text=True, timeout=10,
        )
        if result.returncode or not (destination / 'a-new').is_file():
            test_fail(f'{label}: transfer failed: {result.returncode}\n{result.stdout}{result.stderr}')
        rows = [(3600 * hours + 60 * minutes + seconds, left)
                for hours, minutes, seconds, left in
                (map(int, row) for row in COMPLETED_ROW.findall(result.stdout))]
        if (len(rows) < 2 or rows[0][1] == 0 or rows[-1][1] != 0
                or not 0 < rows[-1][0] < rows[0][0]):
            test_fail(f'{label}: expected an ETA after the first file and elapsed time at the end\n'
                      f'{result.stdout}{result.stderr}')


if __name__ == '__main__':
    run(test)
