#!/usr/bin/env python3
# Regression test for secure_relative_open()'s resolution semantics,
# which must be identical across the resolver tiers (the kernel
# RESOLVE_BENEATH fast paths and the per-component O_NOFOLLOW walk
# fallback used on NetBSD, OpenBSD, Solaris, Cygwin, pre-5.6 Linux
# and --disable-openat2 builds):
#
#   - a missing FINAL component with O_CREAT is created (regression:
#     the walk fallback returned ENOENT instead, which broke every
#     new-file create through the non-chroot daemon receiver's
#     --inplace path, e.g. MariaDB/Galera rsync SST);
#   - a missing INTERMEDIATE component still fails with ENOENT;
#   - an existing regular file opens (O_RDONLY, and O_WRONLY|O_CREAT
#     without O_EXCL);
#   - an out-of-tree symlink in the final component is refused and
#     O_CREAT does not create the escape target;
#   - O_DIRECTORY opens an existing directory and stays ENOENT for a
#     missing name (no creation for directory requests).
#
# The checks live in the t_secure_relpath helper ('semantics' mode).

import subprocess

from rsyncfns import SCRATCHDIR, TOOLDIR, rmtree, test_fail


testdir = SCRATCHDIR / 'secure-open-test'
rmtree(testdir)
testdir.mkdir(parents=True)

proc = subprocess.run([str(TOOLDIR / 't_secure_relpath'), str(testdir), 'semantics'])
if proc.returncode != 0:
    test_fail(
        'secure_relative_open() resolution semantics check failed '
        '(see stderr above for the specific case)'
    )
