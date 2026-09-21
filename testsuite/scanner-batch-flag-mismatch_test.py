#!/usr/bin/python3
# Regression test for run5 0016: a --read-batch whose recorded stream-flags
# enable a metadata class (xattrs/acls/uid/gid) that the reading command line
# did not.  setup_protocol() used to compute xattrs_ndx BEFORE check_batch_flags()
# flipped preserve_xattrs on, leaving xattrs_ndx=0 -- so F_XATTR(file) wrote a
# 4-byte int at offset 0 of every file_struct, clobbering file->dirname into a
# wild pointer that flist_sort_and_clean() then dereferenced (heap overflow).
import subprocess
from rsyncfns import (
    FROMDIR, SCRATCHDIR, forced_protocol, makepath, rmtree, run_rsync, rsync_argv,
    test_fail, test_skipped, xattr_set, xattrs_supported,
)

if not xattrs_supported():
    test_skipped("needs xattr support to write an -X batch")

proto = forced_protocol()
if proto is not None and proto < 30:
    test_skipped("xattrs (-X) need protocol 30+")

src = FROMDIR
rmtree(src)
makepath(src)
# Several files, each with an xattr, so the post-clobber flist_sort_and_clean()
# walks file->dirname across many entries.  Some platforms advertise xattr
# support yet reject a user-namespace xattr on a plain file (e.g. FreeBSD's
# setextattr) -- skip cleanly there rather than fail in setup.
for i in range(40):
    f = src / f'f{i:03d}'
    f.write_text('x\n')
    try:
        xattr_set('user.m', 'v', f)
    except Exception as e:
        test_skipped(f"cannot set a user xattr on a regular file here: {e}")

batch = SCRATCHDIR / 'xbatch'
wb_dest = SCRATCHDIR / 'wb-dest'
rmtree(wb_dest)
# Write the batch WITH -X: the stream-flags record preserve_xattrs.
run_rsync('-a', '-X', f'--only-write-batch={batch}', f'{src}/', str(wb_dest))

dest = SCRATCHDIR / 'rb-dest'
rmtree(dest)
makepath(dest)
# Replay with BARE -a (no -X): the batch flips preserve_xattrs on after the
# *_ndx were computed.  Must complete cleanly now.
r = subprocess.run(rsync_argv('-a', f'--read-batch={batch}', str(dest)),
                   capture_output=True, text=True)
if r.returncode < 0 or r.returncode >= 128:
    test_fail(f'read-batch crashed (rc={r.returncode}): {r.stderr.strip()[:200]}')
if not (dest / 'f000').exists():
    test_fail(f'read-batch did not replay the files (rc={r.returncode}): '
              f'{r.stderr.strip()[:200]}')
print("scanner-batch-flag-mismatch: -X batch replayed with bare -a is clean")
