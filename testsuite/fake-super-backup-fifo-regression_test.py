#!/usr/bin/env python3
"""Fake-super backup fallback must retain an inert FIFO placeholder."""

import os
import platform
import stat
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped, xattrs_supported,
)


system = platform.system()
if system not in ('Darwin', 'Linux'):
    test_skipped('deterministic fast-path failure needs dyld/ELF interposing')
if not xattrs_supported():
    test_skipped('fake-super FIFO setup requires xattr support')

base = (SCRATCHDIR / 'fake-super-backup-fifo-regression').resolve()
rmtree(base)
src = base / 'src'
mod = base / 'mod'
dest_sub = mod / 'slot'
backup_root = base / 'backup'
backup_sub = backup_root / 'slot'
load_marker = base / 'hook-loaded'
fallback_marker = base / 'backup-fast-path-failed'
for directory in (src / 'slot', dest_sub, backup_sub):
    directory.mkdir(parents=True)

source_victim = src / 'slot' / 'victim'
os.mkfifo(source_victim, 0o600)

# Force both backup fast paths to fail so production code must reconstruct the
# existing fake-super FIFO in --backup-dir.  The interposer changes only this
# scheduling/error precondition; rsync itself chooses and creates the result.
hook_src = base / 'fake-super-backup-hook.c'
hook_lib = base / ('fake-super-backup-hook.dylib'
                   if system == 'Darwin' else 'fake-super-backup-hook.so')
hook_src.write_text(r'''
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int claim(const char *path)
{
    int fd;
    if (!path || (fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600)) < 0)
        return 0;
    close(fd);
    return 1;
}

static const char *leaf(const char *path)
{
    const char *slash = path ? strrchr(path, '/') : NULL;
    return slash ? slash + 1 : path;
}

static int victim_pair(const char *oldname, const char *newname)
{
    oldname = leaf(oldname);
    newname = leaf(newname);
    return oldname && newname && strcmp(oldname, "victim") == 0
        && strcmp(newname, "victim") == 0;
}

static int fail_pair(const char *oldname, const char *newname, int err)
{
    if (victim_pair(oldname, newname)
     && claim(getenv("RSYNC_FAKE_BACKUP_LINK_MARKER"))) {
        errno = err;
        return 1;
    }
    return 0;
}

static int hook_link(const char *oldname, const char *newname)
{
    if (fail_pair(oldname, newname, EPERM))
        return -1;
#ifdef SYS_link
    return (int)syscall(SYS_link, oldname, newname);
#else
    return (int)syscall(SYS_linkat, AT_FDCWD, oldname,
                        AT_FDCWD, newname, 0);
#endif
}

static int hook_rename(const char *oldname, const char *newname)
{
    if (fail_pair(oldname, newname, EXDEV))
        return -1;
#ifdef SYS_rename
    return (int)syscall(SYS_rename, oldname, newname);
#else
    return (int)syscall(SYS_renameat, AT_FDCWD, oldname,
                        AT_FDCWD, newname);
#endif
}

static int hook_linkat(int oldfd, const char *oldname,
                       int newfd, const char *newname, int flags)
{
    if (fail_pair(oldname, newname, EPERM))
        return -1;
    return (int)syscall(SYS_linkat, oldfd, oldname, newfd, newname, flags);
}

static int hook_renameat(int oldfd, const char *oldname,
                         int newfd, const char *newname)
{
    if (fail_pair(oldname, newname, EXDEV))
        return -1;
    return (int)syscall(SYS_renameat, oldfd, oldname, newfd, newname);
}

__attribute__((constructor)) static void hook_loaded(void)
{
    claim(getenv("RSYNC_FAKE_BACKUP_LOAD_MARKER"));
}

#ifdef __APPLE__
__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} interposers[] __attribute__((section("__DATA,__interpose"))) = {
    { (const void *)hook_link, (const void *)link },
    { (const void *)hook_rename, (const void *)rename },
    { (const void *)hook_linkat, (const void *)linkat },
    { (const void *)hook_renameat, (const void *)renameat },
};
#else
int link(const char *oldname, const char *newname)
{
    return hook_link(oldname, newname);
}

int rename(const char *oldname, const char *newname)
{
    return hook_rename(oldname, newname);
}

int linkat(int oldfd, const char *oldname,
           int newfd, const char *newname, int flags)
{
    return hook_linkat(oldfd, oldname, newfd, newname, flags);
}

int renameat(int oldfd, const char *oldname,
             int newfd, const char *newname)
{
    return hook_renameat(oldfd, oldname, newfd, newname);
}
#endif
''')

build_args = (['cc', '-dynamiclib'] if system == 'Darwin'
              else ['cc', '-shared', '-fPIC'])
build = subprocess.run(
    [*build_args, '-o', str(hook_lib), str(hook_src)],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if build.returncode != 0:
    test_skipped(f'cannot build dyld interposer: {build.stdout!r}')

seed = subprocess.run(
    rsync_argv('-rtI', '--specials', '--fake-super', f'{src}/', f'{mod}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if seed.returncode != 0:
    test_fail(f'initial fake-super FIFO upload failed: {seed.stdout!r}')
placeholder = dest_sub / 'victim'
if not stat.S_ISREG(os.lstat(placeholder).st_mode):
    test_fail('initial FIFO was not represented by an inert regular file')

source_victim.unlink()
source_victim.write_text('NEW-REMOTE-DATA\n')

result = subprocess.run(
    rsync_argv('-rtI', '--specials', '--fake-super', '--backup',
               f'--backup-dir={backup_root}', '--no-inc-recursive',
               f'{src}/', f'{mod}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    env={**os.environ,
         ('DYLD_INSERT_LIBRARIES' if system == 'Darwin' else 'LD_PRELOAD'):
             str(hook_lib),
         'RSYNC_FAKE_BACKUP_LOAD_MARKER': str(load_marker),
         'RSYNC_FAKE_BACKUP_LINK_MARKER': str(fallback_marker)})
if result.returncode != 0:
    test_fail(f'fake-super backup failed: {result.stdout!r}')
if not load_marker.exists() or not fallback_marker.exists():
    test_fail(f'positive control did not force the backup fallback: {result.stdout!r}')

backup = backup_sub / 'victim'
backup_mode = os.lstat(backup).st_mode
if stat.S_ISFIFO(backup_mode):
    test_fail('fake-super backup materialized a live FIFO')
if not stat.S_ISREG(backup_mode):
    test_fail(f'unexpected fake-super backup type: {backup_mode:o}')

print('fake-super backup remained an inert regular placeholder')
