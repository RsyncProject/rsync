#!/usr/bin/env python3
"""PoC: EACCES recovery must retain partial-dir ownership policy (Linux).

Linux twin of partial-protected-regular-retry-policy_test.py, which runs only
under dyld interposing.  It covers the recovery arm that test cannot reach:
the fs.protected_regular compatibility retry, i.e. the "#ifdef linux" arm of
recv_files(), which is not compiled on Darwin.  Between them the two tests
cover both arms (Darwin's first EACCES goes straight to the generic
read-only-file chmod-and-reopen recovery, there being no Linux arm there).

The one-inplace partial leaf is first opened through the operator ownership
walk.  The hook models an EACCES from that O_CREAT open and swaps the partial
directory for a symlink in the exact recovery window.  A retry that drops to
the ordinary resolver follows the new parent with no ownership check and
writes the remote payload into a different file; one that keeps the walk
refuses the foreign-owned symlink.
"""

import os
import platform
import subprocess

from rsyncfns import (
    SCRATCHDIR, forced_protocol, rmtree, rsync_argv, test_fail, test_skipped,
)


if platform.system() != 'Linux':
    test_skipped('LD_PRELOAD partial EACCES recovery hook is Linux-only')

# one_inplace staging needs inplace_partial, which is negotiated through the
# protocol-30 CF_INPLACE_PARTIAL_DIR capability flag.  Below that the receiver
# uses the ordinary tmpfile path and the recovery under test never runs.
_proto = forced_protocol()
if _proto is not None and _proto < 30:
    test_skipped(f'one-inplace partial staging needs protocol >= 30 (forced {_proto})')

hook_code = r'''
#define _GNU_SOURCE
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int swapped;

static int (*real_open)(const char *, int, ...);
static int (*real_openat)(int, const char *, int, ...);
static int (*real_fstatat)(int, const char *, struct stat *, int);
static int (*real_fxstatat)(int, int, const char *, struct stat *, int);

/* Resolve on demand rather than trusting our constructor to have run.  A
 * preloaded open() interposes for the whole process the moment the loader maps
 * us, including calls made from OTHER libraries' constructors -- and the order
 * constructors run between unrelated shared objects is unspecified.  On
 * AlmaLinux 8, OPENSSL_init_library() calls open() from its constructor before
 * ours runs, so a plain "resolved in the constructor" pointer is still NULL and
 * the process dies with SIGSEGV inside the loader. */
/* If dlsym() itself reaches an interposed function, the nested wrapper must
 * not recurse back into resolution -- it takes the raw path instead. */
static __thread int hook_resolving;

static void hook_resolve(void)
{
    if (hook_resolving)
        return;
    hook_resolving = 1;
    if (!real_open)     real_open     = dlsym(RTLD_NEXT, "open");
    if (!real_openat)   real_openat   = dlsym(RTLD_NEXT, "openat");
    if (!real_fstatat)  real_fstatat  = dlsym(RTLD_NEXT, "fstatat");
    if (!real_fxstatat) real_fxstatat = dlsym(RTLD_NEXT, "__fxstatat");
    hook_resolving = 0;
}

/* Last-resort passthrough if even dlsym() is unusable this early.  openat(2)
 * rather than open(2): open is not a syscall on every architecture. */
static int raw_openat(int dfd, const char *path, int flags, mode_t mode)
{
    return (int)syscall(SYS_openat, dfd, path, flags, mode);
}

static void mark(const char *path)
{
    int fd;
    hook_resolve();
    if (!path)
        return;
    fd = real_open ? real_open(path, O_WRONLY | O_CREAT | O_EXCL, 0600)
                   : raw_openat(AT_FDCWD, path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd >= 0)
        close(fd);
}

/* Darwin's F_GETPATH equivalent. */
static int fd_path_is(int fd, const char *wanted)
{
    char procpath[64], path[PATH_MAX];
    ssize_t n;

    if (!wanted || fd < 0)
        return 0;
    snprintf(procpath, sizeof procpath, "/proc/self/fd/%d", fd);
    n = readlink(procpath, path, sizeof path - 1);
    if (n < 0)
        return 0;
    path[n] = '\0';
    return strcmp(path, wanted) == 0;
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

/* Swap the partial dir for a symlink and report EACCES, exactly as
 * fs.protected_regular would on the O_CREAT open of an existing leaf. */
static int swap_and_deny(void)
{
    const char *partial = getenv("RSYNC_PARTIAL_RETRY_DIR");
    const char *held = getenv("RSYNC_PARTIAL_RETRY_HELD");
    if (partial && held && rename(partial, held) == 0
        && symlink("secret", partial) == 0) {
        swapped = 1;
        mark(getenv("RSYNC_PARTIAL_RETRY_EACCES_MARKER"));
        errno = EACCES;
        return -1;
    }
    restore_partial();
    return 0;   /* caller falls through to the real call */
}

#ifdef O_TMPFILE
/* O_TMPFILE is (__O_TMPFILE | O_DIRECTORY) on Linux, so a plain & test would
 * also match an ordinary O_DIRECTORY open. */
# define HOOK_TAKES_MODE(f) (((f) & O_CREAT) || (((f) & O_TMPFILE) == O_TMPFILE))
#else
# define HOOK_TAKES_MODE(f) ((f) & O_CREAT)
#endif

static int is_victim_write(const char *path, int flags)
{
    return path && strcmp(path, "victim") == 0
        && (flags & O_ACCMODE) == O_WRONLY;
}

int openat(int dfd, const char *path, int flags, ...)
{
    const char *partial, *secret;
    mode_t mode = 0;
    int fd, saved_errno;

    hook_resolve();
    partial = getenv("RSYNC_PARTIAL_RETRY_DIR");
    secret = getenv("RSYNC_PARTIAL_RETRY_SECRET");

    if (HOOK_TAKES_MODE(flags)) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }

    if (!swapped && is_victim_write(path, flags) && (flags & O_CREAT)
        && fd_path_is(dfd, partial)) {
        if (swap_and_deny() < 0)
            return -1;
    }

    fd = real_openat ? real_openat(dfd, path, flags, mode)
                     : raw_openat(dfd, path, flags, mode);
    saved_errno = errno;

    /* An unconfined retry resolves the swapped parent and lands in the secret
     * dir.  Pin the evidence, then restore the name so later finalization
     * cannot disguise which file was written. */
    if (swapped && fd >= 0 && is_victim_write(path, flags)
        && !(flags & O_CREAT) && fd_path_is(dfd, secret)) {
        mark(getenv("RSYNC_PARTIAL_RETRY_OPEN_MARKER"));
        restore_partial();
    }
    errno = saved_errno;
    return fd;
}

int open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    int fd, saved_errno;

    hook_resolve();

    if (HOOK_TAKES_MODE(flags)) {
        va_list ap;
        va_start(ap, flags);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
    }

    if (!swapped && path && strstr(path, "pdir/victim")
        && (flags & O_ACCMODE) == O_WRONLY && (flags & O_CREAT)) {
        if (swap_and_deny() < 0)
            return -1;
    }

    fd = real_open ? real_open(path, flags, mode)
                   : raw_openat(AT_FDCWD, path, flags, mode);
    saved_errno = errno;

    /* The pre-fix recovery uses plain open() on the full pathname, which walks
     * the swapped symlink with no ownership check. */
    if (swapped && fd >= 0 && path && strstr(path, "pdir/victim")
        && (flags & O_ACCMODE) == O_WRONLY && !(flags & O_CREAT)) {
        mark(getenv("RSYNC_PARTIAL_RETRY_OPEN_MARKER"));
        restore_partial();
    }
    errno = saved_errno;
    return fd;
}

/* ona_open() decides via fstatat(..., AT_SYMLINK_NOFOLLOW) and refuses a
 * component owned by neither root nor the euid.  Model the attacker as a
 * different uid so a retry that kept the ownership walk refuses the swap. */
static void model_foreign_owner(int rc, const char *path, struct stat *st)
{
    if (rc == 0 && swapped && path && strcmp(path, "pdir") == 0
        && S_ISLNK(st->st_mode)) {
        st->st_uid = geteuid() + 1;
        mark(getenv("RSYNC_PARTIAL_RETRY_FOREIGN_MARKER"));
    }
}

int fstatat(int dfd, const char *path, struct stat *st, int flags)
{
    int rc, saved_errno;

    hook_resolve();
    if (!real_fstatat) {
        errno = ENOSYS;
        return -1;
    }
    rc = real_fstatat(dfd, path, st, flags);
    saved_errno = errno;
    model_foreign_owner(rc, path, st);
    errno = saved_errno;
    return rc;
}

/* glibc < 2.33 routes fstatat() through __fxstatat(). */
int __fxstatat(int ver, int dfd, const char *path, struct stat *st, int flags)
{
    int rc, saved_errno;

    hook_resolve();
    if (!real_fxstatat) {
        errno = ENOSYS;
        return -1;
    }
    rc = real_fxstatat(ver, dfd, path, st, flags);
    saved_errno = errno;
    model_foreign_owner(rc, path, st);
    errno = saved_errno;
    return rc;
}

__attribute__((constructor)) static void hook_loaded(void)
{
    hook_resolve();
    mark(getenv("RSYNC_PARTIAL_RETRY_LOAD_MARKER"));
}

__attribute__((destructor)) static void hook_unload(void)
{
    restore_partial();
}
'''


def run():
    base = SCRATCHDIR / 'partial-protected-regular-retry-linux'
    rmtree(base)
    src = base / 'src'
    mod = base / 'mod'
    partial = mod / 'pdir'
    held = mod / '.pdir-held'
    secret = mod / 'secret'
    markers = {
        'load': base / 'hook-loaded',
        'eacces': base / 'operator-open-eacces',
        'retry': base / 'unconfined-retry-open',
        'foreign': base / 'owner-check-would-refuse',
    }
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

    hook_src = base / 'hook.c'
    hook_lib = base / 'hook.so'
    hook_src.write_text(hook_code)
    build = subprocess.run(
        ['cc', '-shared', '-fPIC', '-o', str(hook_lib), str(hook_src), '-ldl'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if build.returncode != 0:
        test_skipped(f'cannot build LD_PRELOAD partial retry hook: {build.stdout!r}')

    run_env = os.environ.copy()
    run_env.update({
        'LD_PRELOAD': str(hook_lib),
        'RSYNC_PARTIAL_RETRY_DIR': str(partial),
        'RSYNC_PARTIAL_RETRY_HELD': str(held),
        'RSYNC_PARTIAL_RETRY_SECRET': str(secret),
        'RSYNC_PARTIAL_RETRY_LOAD_MARKER': str(markers['load']),
        'RSYNC_PARTIAL_RETRY_EACCES_MARKER': str(markers['eacces']),
        'RSYNC_PARTIAL_RETRY_OPEN_MARKER': str(markers['retry']),
        'RSYNC_PARTIAL_RETRY_FOREIGN_MARKER': str(markers['foreign']),
    })

    result = subprocess.run(
        rsync_argv('-rtI', '--partial', '--partial-dir=pdir',
                   '--no-inc-recursive', f'{src}/', f'{mod}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=run_env)

    if held.exists():
        if partial.is_symlink():
            partial.unlink()
        held.rename(partial)

    ctx = f'rc={result.returncode}, output={result.stdout!r}'
    # A negative returncode is death by signal.  That is never a reason to skip:
    # the hook killing the process under test is a bug in the hook, and reporting
    # it as "not loaded" is exactly how a SIGSEGV on AlmaLinux 8 hid for weeks --
    # the marker is written by the hook, so a crash before that looks identical
    # to the hook never having loaded.
    if result.returncode is not None and result.returncode < 0:
        test_fail(f'rsync died by signal {-result.returncode} under the '
                  f'LD_PRELOAD hook ({ctx})')
    if not markers['load'].exists():
        test_skipped(f'LD_PRELOAD hook was not loaded ({ctx})')
    if not markers['eacces'].exists():
        test_fail('positive control failed: receiver did not open the existing '
                  f'partial file with O_CREAT ({ctx})')

    # The escape check must come before the ownership-walk control below: on a
    # vulnerable build no walk runs at all, and that has to be reported as the
    # escape it is rather than as an inconclusive control failure.
    observed = (secret / 'victim').read_bytes()
    if observed == new_source:
        test_fail('EACCES recovery dropped partial-dir ownership policy and '
                  f'overwrote a different file through the raced symlink ({ctx})')
    if observed != secret_data:
        test_fail(f'outside-policy target has unexpected contents ({ctx})')
    if markers['retry'].exists():
        test_fail('an unconfined retry opened the victim through the raced '
                  f'partial-dir symlink ({ctx})')

    # Anti-vacuity: nothing was overwritten -- prove that is the ownership walk
    # refusing the swap, not the scenario failing to arm.
    if not markers['foreign'].exists():
        test_fail('inconclusive: the recovery never inspected the raced '
                  f'partial-dir symlink, so no ownership check ran ({ctx})')


run()
print('protected_regular compatibility retry retained partial-dir ownership policy')
