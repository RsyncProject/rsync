#!/usr/bin/env python3

import os
import stat
import struct

from rsyncfns import SCRATCHDIR, makepath, run_rsync, test_fail

source = SCRATCHDIR / 'source'
destination = SCRATCHDIR / 'destination'
makepath(source)
makepath(destination)
target = destination / 'target'
target.write_text('target contents\n')
target.chmod(0o640)
links = [('link', 'target'), ('dangling', 'missing')]
for name, link in links:
    os.symlink(link, source / name)

batch = SCRATCHDIR / 'symlinks.batch'
run_rsync('-rlp', '--checksum-seed=1', f'--only-write-batch={batch}',
          f'{source}/', f'{destination}/')

# Supply BSD-style link modes without relying on the source filesystem
mode = stat.S_IMODE((source / 'link').lstat().st_mode)
wanted = 0o400 if mode != 0o400 else 0o755
encoded_mode = struct.pack('<I', 0o120000 | mode)
data = batch.read_bytes()
if data.count(encoded_mode) != 1:
    test_fail('expected one shared symlink mode in the batch')
batch.write_bytes(data.replace(encoded_mode, struct.pack('<I', 0o120000 | wanted)))

for _ in range(2):
    run_rsync('-rlp', f'--read-batch={batch}', f'{destination}/')
    for name, link in links:
        if not (destination / name).is_symlink() or os.readlink(destination / name) != link:
            test_fail(f'{name}: symlink was not preserved')
    if stat.S_IMODE(target.stat().st_mode) != 0o640 or target.read_text() != 'target contents\n':
        test_fail('symlink chmod changed the target')
    if (destination / 'missing').exists():
        test_fail('dangling symlink target was created')
