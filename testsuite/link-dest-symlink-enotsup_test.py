#!/usr/bin/env python3
"""--link-dest must fall back to a copy when the destination refuses to
hard-link a symlink, whatever the errno, and must report it only once.

CAN_HARDLINK_SYMLINK is decided by configure running linkat() on whatever
filesystem the build tree sat on.  The destination is free to disagree -- macOS
builds on APFS and backs up to HFS+, which answers ENOTSUP -- and a build that
says yes had no fallback left, so the transfer died with exit 23 even though the
symlink was then created correctly.  Every neighbouring case copes: a regular
file whose link() fails falls back, and so does a build compiled without the
capability.

Forcing the errno with LD_PRELOAD reaches that arm on a filesystem that can
hard-link symlinks perfectly well.
"""

import os
import re
import platform
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, run_rsync, rsync_argv, test_fail,
    test_skipped,
)

if platform.system() != 'Linux':
    test_skipped('the LD_PRELOAD linkat hook is Linux-only')

vv = run_rsync('-VV', check=True, capture_output=True).stdout
if '"hardlink_symlinks": true' not in vv:
    test_skipped('build cannot hard-link symlinks, so it already falls back '
                 'at compile time and this arm is unreachable')

hook_code = r'''
#define _GNU_SOURCE
#include <sys/stat.h>
#include <sys/types.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int (*real_link)(const char *, const char *);
static int (*real_linkat)(int, const char *, int, const char *, int);

/* Resolved on demand rather than in a constructor: link()/linkat() are not
 * called during loader initialisation, but resolving lazily costs nothing and
 * keeps this hook out of the constructor-ordering trap the partial-retry hook
 * documents. */
static void hook_resolve(void)
{
    if (!real_link)   real_link   = dlsym(RTLD_NEXT, "link");
    if (!real_linkat) real_linkat = dlsym(RTLD_NEXT, "linkat");
}

static void mark(const char *var)
{
    const char *path = getenv(var);
    int fd;
    if (!path)
        return;
    fd = open(path, O_WRONLY | O_CREAT, 0600);
    if (fd >= 0)
        close(fd);
}

static int refuse_errno(void)
{
    const char *e = getenv("RSYNC_LINK_REFUSE_ERRNO");
    if (e && strcmp(e, "EPERM") == 0)
        return EPERM;
    if (e && strcmp(e, "ENOSYS") == 0)
        return ENOSYS;
    return ENOTSUP;
}

/* Only a SYMLINK source is refused: the regular files in the same transfer
 * must keep hard-linking, or the test could not tell "fell back for the
 * symlink" from "gave up on --link-dest entirely". */
static int is_symlink_at(int dfd, const char *path)
{
    struct stat st;
    if (!path)
        return 0;
    if (fstatat(dfd, path, &st, AT_SYMLINK_NOFOLLOW) < 0)
        return 0;
    return S_ISLNK(st.st_mode);
}

int linkat(int olddirfd, const char *oldpath, int newdirfd,
           const char *newpath, int flags)
{
    hook_resolve();
    if (is_symlink_at(olddirfd, oldpath)) {
        mark("RSYNC_LINK_HOOK_MARKER");
        errno = refuse_errno();
        return -1;
    }
    return real_linkat(olddirfd, oldpath, newdirfd, newpath, flags);
}

int link(const char *oldpath, const char *newpath)
{
    hook_resolve();
    if (is_symlink_at(AT_FDCWD, oldpath)) {
        mark("RSYNC_LINK_HOOK_MARKER");
        errno = refuse_errno();
        return -1;
    }
    return real_link(oldpath, newpath);
}
'''

base = SCRATCHDIR / 'link-dest-symlink-enotsup'
rmtree(base)
src = base / 'src'
ldest = base / 'ldest'
makepath(src, ldest)

# The basis must match exactly (match_level 3) or try_dests_non() never
# reaches the hard-link at all.
for d in (src, ldest):
    (d / 'reg').write_bytes(b'REGULAR-CONTENT')
    os.symlink('some-target', d / 'sym')

hook_src = base / 'hook.c'
hook_lib = base / 'hook.so'
hook_src.write_text(hook_code)
build = subprocess.run(
    ['cc', '-shared', '-fPIC', '-o', str(hook_lib), str(hook_src), '-ldl'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if build.returncode != 0:
    test_skipped(f'cannot build the LD_PRELOAD linkat hook: {build.stdout!r}')


def run(dest, refuse, args=('-a',)):
    marker = base / f'fired-{refuse}-{dest.name}'
    env = os.environ.copy()
    env.update({
        'LD_PRELOAD': str(hook_lib),
        'RSYNC_LINK_REFUSE_ERRNO': refuse,
        'RSYNC_LINK_HOOK_MARKER': str(marker),
    })
    r = subprocess.run(
        rsync_argv(*args, f'--link-dest={ldest}', f'{src}/', f'{dest}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if r.returncode is not None and r.returncode < 0:
        test_fail(f'rsync died by signal {-r.returncode} under the hook: '
                  f'output={r.stdout!r}')
    if not marker.exists():
        test_skipped(f'the LD_PRELOAD hook never fired (rc={r.returncode}, '
                     f'output={r.stdout!r})')
    return r


def check_fallback(dest, refuse):
    got = run(dest, refuse)
    ctx = f'refuse={refuse}, rc={got.returncode}, output={got.stdout!r}'
    assert got.returncode == 0, (
        'a destination that refused to hard-link a symlink failed the transfer '
        f'instead of falling back to a copy: {ctx}')
    assert 'failed to hard-link' not in got.stdout, (
        f'the fallback still reported a hard-link failure: {ctx}')
    assert (dest / 'sym').is_symlink() and os.readlink(dest / 'sym') == 'some-target', (
        f'the symlink was not recreated by the fallback: {ctx}')
    # The regular file must still be hard-linked, or "it fell back" would also
    # be satisfied by --link-dest having been abandoned altogether.
    assert (dest / 'reg').stat().st_ino == (ldest / 'reg').stat().st_ino, (
        f'the regular file stopped being hard-linked to the basis: {ctx}')


# ENOTSUP is the answer configure would have given, arriving late.
check_fallback(base / 'dest-enotsup', 'ENOTSUP')

# EPERM and ENOSYS reach the same place.  errno cannot separate "this
# filesystem has no hard links" from "you may not": link(2) documents EPERM for
# both, and FUSE reports ENOSYS for a filesystem that omits the callback.  The
# regular-file path next door has always fallen back on every errno, so these
# do too -- the entry is created correctly either way.
check_fallback(base / 'dest-eperm', 'EPERM')
check_fallback(base / 'dest-enosys', 'ENOSYS')

# The fallback must report the entry ONCE.  try_dests_non() itemises the basis
# match itself, so a caller that then creates the entry with itemising still on
# prints it twice -- which is what a plain -a run cannot see, and what an
# itemised --link-dest backup on such a filesystem would show.
dest = base / 'dest-itemize'
got = run(dest, 'ENOTSUP', args=('-ivvplrtH',))
ctx = f'rc={got.returncode}, output={got.stdout!r}'
assert got.returncode == 0, (
    f'the itemised run did not succeed, so its output proves nothing: {ctx}')
# Anchored on the change-type letters as well as the name: counting lines that
# merely contain "sym" and "->" would also be satisfied by one warning line, or
# by a single line from a run that then failed.
sym_lines = [ln for ln in got.stdout.splitlines()
             if re.match(r'^cL\S*\s+sym -> some-target$', ln)]
assert len(sym_lines) == 1, (
    f'expected exactly one "cL... sym -> some-target" itemisation, got '
    f'{len(sym_lines)}: {sym_lines} ({ctx})')
