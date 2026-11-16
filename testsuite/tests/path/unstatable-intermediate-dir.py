#!/usr/bin/env python3

import errno
import os
import subprocess

from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.mutation import build_patched_rsync
from harness.results import test_fail
from harness.rsync import rsync_argv_for
from harness import metadata

metadata(features={'dirfd_pinning', 'native_build', 'symlink'}, transports={'pipe'},
         mutates={'filesystem', 'process'}, tags={'path', 'regression', 'security'})

PATCH_OLD = '\t\tif (fstatat(dfd, comp, &lst, AT_SYMLINK_NOFOLLOW) < 0) {'
PATCH_NEW = '''\t\tif ((strcmp(comp, "emulated") == 0
\t\t  ? (errno = ENOENT, -1)
\t\t  : fstatat(dfd, comp, &lst, AT_SYMLINK_NOFOLLOW)) < 0) {'''
probe_rsync = build_patched_rsync(
    'unstatable-intermediate-rsync', [('syscall.c', PATCH_OLD, PATCH_NEW)])

base = SCRATCHDIR / 'unstatable-intermediate-dir'
src = base / 'src'
storage = base / 'storage'
storage_root = storage / 'emulated' / '0'
docs = storage_root / 'Documents'
dest = base / 'dest'

def reset():
    rmtree(base)
    for path in (src, docs, dest):
        path.mkdir(parents=True)

def run(*args, cwd=None):
    return subprocess.run(
        rsync_argv_for(probe_rsync, '-I', '-i', *map(str, args)),
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

def require_copy(label, destination, cwd=None):
    source = src / 'file'
    source.write_text(label + '\n')
    result = run(source, destination, cwd=cwd)
    copied = docs / 'file'
    if result.returncode != 0 or not copied.is_file() or copied.read_text() != label + '\n':
        test_fail(f'{label}: transfer failed: rc={result.returncode}, stderr={result.stderr!r}')

reset()
require_copy('absolute', str(docs) + '/')

reset()
require_copy('relative', './', cwd=docs)

reset()
source = src / 'file'
source.write_text('missing\n')
missing = storage / 'emulated' / '999' / 'Documents'
result = run(source, str(missing) + '/')
if (result.returncode == 0 or missing.exists()
        or os.strerror(errno.ENOENT).encode() not in result.stderr):
    test_fail(f'missing descendant did not fail with ENOENT: {result.stderr!r}')

reset()
source = src / 'file'
source.write_text('symlink\n')
real_storage = base / 'real-storage'
(real_storage / '0' / 'Documents').mkdir(parents=True)
rmtree(storage / 'emulated')
(storage / 'emulated').symlink_to(real_storage)
result = run(source, str(docs) + '/')
errors = (os.strerror(errno.ENOTDIR).encode(), os.strerror(errno.ELOOP).encode())
if result.returncode == 0 or not any(error in result.stderr for error in errors):
    test_fail(f'intermediate symlink was not refused with the openat error: {result.stderr!r}')
if os.strerror(errno.ENOENT).encode() in result.stderr:
    test_fail(f'openat error was replaced with ENOENT: {result.stderr!r}')

reset()
outside = base / 'outside'
outside.mkdir()
(outside / 'secret').write_text('secret\n')
(storage_root / 'escape').symlink_to(outside)
files_from = storage_root / 'files.list'
files_from.write_text('escape/secret\n')
result = run(
    '-r', f'--confine-root={storage_root}', f'--files-from={files_from}',
    storage_root, str(dest) + '/',
)
if (result.returncode == 0 or (dest / 'secret').exists()
        or os.strerror(errno.ELOOP).encode() not in result.stderr):
    test_fail(f'confinement did not reject the escape: {result.stderr!r}')

print('unstatable-intermediate-dir: hidden intermediate directories remain confined')
