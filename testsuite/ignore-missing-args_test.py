#!/usr/bin/env python3
"""Exercise --ignore-missing-args for direct and --files-from sources."""

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_path_arg, rsh_cmd, run_rsync,
    test_fail,
)


base = SCRATCHDIR / 'ignore-missing-args'
src = base / 'src'
dest = base / 'dest'
existing = src / 'deep' / 'existing'
missing = src / 'deep' / 'missing'
rmtree(base)
makepath(existing.parent, dest)
existing.write_text('retained\n')


def assert_existing(path, label):
    if not path.is_file() or path.read_text() != 'retained\n':
        test_fail(f'{label}: existing source was not transferred')


control = run_rsync('-r', str(existing), str(missing), f'{dest}/',
                    check=False, capture_output=True)
if control.returncode != 23:
    test_fail('missing source control did not report a partial transfer: '
              f'rc={control.returncode}\n{control.stderr}')

rmtree(dest)
makepath(dest)
run_rsync('-r', '--ignore-missing-args', str(existing), str(missing),
          f'{dest}/')
assert_existing(dest / 'existing', 'local arguments')

rmtree(dest)
makepath(dest)
ssh = rsh_cmd()
run_rsync('-r', '--ignore-missing-args', '-e', ssh,
          f'--rsync-path={rsync_path_arg()}',
          f'localhost:{existing}', f'localhost:{missing}', f'{dest}/')
assert_existing(dest / 'existing', 'remote-shell arguments')

rmtree(dest)
makepath(dest)
files_from = base / 'files-from'
files_from.write_text('deep/existing\ndeep/missing\n')
run_rsync('-r', '--ignore-missing-args', f'--files-from={files_from}',
          f'{src}/', f'{dest}/')
assert_existing(dest / 'deep' / 'existing', '--files-from arguments')

if (dest / 'deep' / 'missing').exists():
    test_fail('--ignore-missing-args created the absent source')

print('--ignore-missing-args suppresses only missing initial source arguments')
