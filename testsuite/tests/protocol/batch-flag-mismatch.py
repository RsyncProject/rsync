#!/usr/bin/python3

import subprocess
from harness.environment import FROMDIR, SCRATCHDIR
from harness.filesystem import makepath, rmtree
from harness.results import test_fail, test_skipped
from harness.rsync import forced_protocol, rsync_argv, run_rsync
from harness.xattrs import set_value as xattr_set, is_supported as xattrs_supported
from harness import metadata

metadata(features={'protocol_30', 'xattr_runtime'}, transports={'pipe'})

if not xattrs_supported():
    test_skipped("needs xattr support to write an -X batch", capability='xattr_runtime')

proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped("xattrs (-X) need protocol 30+", capability='protocol_30')

src = FROMDIR
rmtree(src)
makepath(src)
for i in range(40):
    f = src / f'f{i:03d}'
    f.write_text('x\n')
    xattr_set('marker', 'v', f)

batch = SCRATCHDIR / 'xbatch'
wb_dest = SCRATCHDIR / 'wb-dest'
rmtree(wb_dest)
run_rsync('-a', '-X', f'--only-write-batch={batch}', f'{src}/', str(wb_dest))

dest = SCRATCHDIR / 'rb-dest'
rmtree(dest)
makepath(dest)
r = subprocess.run(rsync_argv('-a', f'--read-batch={batch}', str(dest)),
                   capture_output=True, text=True)
if r.returncode < 0 or r.returncode >= 128:
    test_fail(f'read-batch crashed (rc={r.returncode}): {r.stderr.strip()[:200]}')
if not (dest / 'f000').exists():
    test_fail(f'read-batch did not replay the files (rc={r.returncode}): '
              f'{r.stderr.strip()[:200]}')
print('batch flag mismatch handled')
