#!/usr/bin/env python3

import os
import platform
import subprocess

from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, test_error, test_fail, test_skipped


if platform.system() != 'Linux':
    test_skipped('the LD_PRELOAD link hook is Linux-only', capability='linux_interpose')
if os.environ.get('ASAN_OPTIONS'):
    test_skipped('the LD_PRELOAD link hook conflicts with AddressSanitizer', capability='linux_interpose')

hook_code = r'''
#define _GNU_SOURCE
#include <sys/stat.h>
#include <sys/types.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>

static int (*real_link)(const char *, const char *);
static int (*real_linkat)(int, const char *, int, const char *, int);

static void resolve_hooks(void)
{
    if (!real_link)
        real_link = dlsym(RTLD_NEXT, "link");
    if (!real_linkat)
        real_linkat = dlsym(RTLD_NEXT, "linkat");
}

static int refuse_source(int dirfd, const char *path)
{
    const char *target = getenv("RSYNC_EMLINK_SOURCE");
    struct stat source_st, target_st;
    int fd;

    if (!target || !path
     || fstatat(dirfd, path, &source_st, 0) < 0
     || stat(target, &target_st) < 0
     || source_st.st_dev != target_st.st_dev
     || source_st.st_ino != target_st.st_ino)
        return 0;

    fd = open(getenv("RSYNC_EMLINK_MARKER"), O_WRONLY | O_CREAT, 0600);
    if (fd >= 0)
        close(fd);
    errno = EMLINK;
    return 1;
}

int link(const char *oldpath, const char *newpath)
{
    resolve_hooks();
    if (refuse_source(AT_FDCWD, oldpath))
        return -1;
    return real_link(oldpath, newpath);
}

int linkat(int olddirfd, const char *oldpath, int newdirfd,
           const char *newpath, int flags)
{
    resolve_hooks();
    if (refuse_source(olddirfd, oldpath))
        return -1;
    return real_linkat(olddirfd, oldpath, newdirfd, newpath, flags);
}
'''

base = SCRATCHDIR / 'hardlinks-emlink-recovery'
rmtree(base)
src = base / 'src'
dest = base / 'dest'
makepath(src / 'a', src / 'b', src / 'c', dest / 'a')

(src / 'a' / 'f').write_bytes(b'hard-link data\n')
os.link(src / 'a' / 'f', src / 'b' / 'f')
os.link(src / 'a' / 'f', src / 'c' / 'f')
(dest / 'a' / 'f').write_bytes(b'hard-link data\n')
source_st = (src / 'a' / 'f').stat()
os.utime(dest / 'a' / 'f', ns=(source_st.st_atime_ns, source_st.st_mtime_ns))

hook_src = base / 'hook.c'
hook_lib = base / 'hook.so'
hook_src.write_text(hook_code)
build = subprocess.run(
    ['cc', '-shared', '-fPIC', '-o', str(hook_lib), str(hook_src), '-ldl'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if build.returncode != 0:
    test_error(f'cannot build the LD_PRELOAD link hook: {build.stdout!r}')

marker = base / 'hook-fired'
env = os.environ.copy()
env.update({
    'LD_PRELOAD': str(hook_lib),
    'RSYNC_EMLINK_SOURCE': str(dest / 'a' / 'f'),
    'RSYNC_EMLINK_MARKER': str(marker),
})
result = subprocess.run(
    rsync_argv('-aH', f'{src}/', f'{dest}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

if not marker.exists():
    test_fail(f'EMLINK hook did not fire: rc={result.returncode}, output={result.stdout!r}')
if result.returncode != 23:
    test_fail(f'rsync exited {result.returncode}, expected 23: {result.stdout!r}')

paths = [dest / 'a' / 'f', dest / 'b' / 'f', dest / 'c' / 'f']
missing = [str(path.relative_to(dest)) for path in paths if not path.is_file()]
if missing:
    test_fail(f'hard-link recovery omitted {missing}: {result.stdout!r}')
if any(path.read_bytes() != b'hard-link data\n' for path in paths):
    test_fail('hard-link recovery changed file content')

inos = [path.stat().st_ino for path in paths]
if inos[1] != inos[2]:
    test_fail(f'fallback topology is wrong: inodes={inos}, output={result.stdout!r}')
