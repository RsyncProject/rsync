#!/usr/bin/env python3
"""Regression test for un-stat-able intermediate mount directories.

On Android (and certain container/FUSE mount namespaces), an intermediate
directory such as /storage/emulated fails fstatat(AT_SYMLINK_NOFOLLOW) with
ENOENT even though its mounted child (/storage/emulated/0) resolves and is
accessible. Verify that ona_open() coalesces the un-stat-able parent with its
child while still enforcing --confine-root and daemon module confinement.
"""

import os
import re
import shutil
import subprocess
import sys

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon,
    test_fail, test_skipped, write_daemon_conf,
)

DAEMON_PORT = 12897

if not sys.platform.startswith('linux'):
    test_skipped("LD_PRELOAD fstatat interposer test is Linux/ELF specific")

cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
if not cc:
    test_skipped("No C compiler found to build LD_PRELOAD fstatat interposer")

base = SCRATCHDIR / 'unstatable-intermediate-dir'
rmtree(base)
base.mkdir(parents=True)

# 1. Build a small LD_PRELOAD shared library that simulates Android's
# /storage/emulated behavior: fstatat(dfd, "emulated", ...) returns ENOENT,
# while fstatat(dfd, "emulated/0", ...) passes through to the real kernel syscall.
preload_c = base / 'fake_android_fuse.c'
preload_so = base / 'fake_android_fuse.so'

preload_c.write_text(r'''
#define _GNU_SOURCE
#include <errno.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>

/* Block direct stat/fstatat on bare "emulated" or paths ending in "/emulated",
 * while allowing "emulated/0" (just like Android's /storage/emulated FUSE root). */
static int should_hide(const char *path)
{
    if (!path)
        return 0;
    if (strcmp(path, "emulated") == 0)
        return 1;
    size_t len = strlen(path);
    if (len >= 9 && strcmp(path + len - 9, "/emulated") == 0)
        return 1;
    return 0;
}

static int raw_fstatat(int dirfd, const char *pathname, void *statbuf, int flags)
{
    if (should_hide(pathname)) {
        errno = ENOENT;
        return -1;
    }
#ifdef SYS_newfstatat
    return (int)syscall(SYS_newfstatat, dirfd, pathname, statbuf, flags);
#elif defined(SYS_fstatat64)
    return (int)syscall(SYS_fstatat64, dirfd, pathname, statbuf, flags);
#else
    #error "No fstatat syscall number available"
#endif
}

int fstatat(int dirfd, const char *pathname, void *statbuf, int flags)
{
    return raw_fstatat(dirfd, pathname, statbuf, flags);
}

int fstatat64(int dirfd, const char *pathname, void *statbuf, int flags)
{
    return raw_fstatat(dirfd, pathname, statbuf, flags);
}

int __fxstatat(int ver, int dirfd, const char *pathname, void *statbuf, int flags)
{
    (void)ver;
    return raw_fstatat(dirfd, pathname, statbuf, flags);
}

int __fxstatat64(int ver, int dirfd, const char *pathname, void *statbuf, int flags)
{
    (void)ver;
    return raw_fstatat(dirfd, pathname, statbuf, flags);
}
''')

build = subprocess.run(
    [cc, '-shared', '-fPIC', '-O2', str(preload_c), '-o', str(preload_so)],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
if build.returncode != 0:
    test_skipped(f"Could not build LD_PRELOAD library: {build.stderr.decode('utf-8', 'ignore')}")

# Export into os.environ so start_test_daemon() and all subprocesses inherit it.
os.environ['LD_PRELOAD'] = str(preload_so)
existing_asan = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = (existing_asan + ':verify_asan_link_order=0:detect_leaks=0').lstrip(':')

src = base / 'src'
storage_root = base / 'storage' / 'emulated' / '0'
docs_dir = storage_root / 'Documents'
outside_backup_dir = storage_root / 'backups'
sdcard_link = base / 'sdcard'

confined_root = base / 'confined'
confined_src = confined_root / 'src'
confined_dst = confined_root / 'dst'


def reset_test_dirs():
    """Wipe and recreate all source and destination directories so no state
    can ever leak from one test case into another."""
    rmtree(src)
    rmtree(base / 'storage')
    rmtree(confined_root)
    if sdcard_link.is_symlink() or sdcard_link.exists():
        sdcard_link.unlink()

    src.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    outside_backup_dir.mkdir(parents=True)
    sdcard_link.symlink_to(storage_root)
    confined_src.mkdir(parents=True)
    confined_dst.mkdir(parents=True)


def verify_fresh_transfer(case_name, src_path, dst_arg, expected_dst_file, expected_content, cwd=None, extra_args=()):
    if expected_dst_file.exists():
        expected_dst_file.unlink()
    proc = subprocess.run(
        rsync_argv('-I', '-i', *extra_args, str(src_path), dst_arg),
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        test_fail(f"{case_name} failed: {proc.stderr!r}")
    expected_item = f"f+++++++++ {src_path.name}".encode()
    if expected_item not in proc.stdout:
        test_fail(f"{case_name} did not perform a fresh transfer of {src_path.name} (stdout={proc.stdout!r})")
    if not expected_dst_file.is_file():
        test_fail(f"{case_name} did not create {expected_dst_file}")
    actual = expected_dst_file.read_text()
    if actual != expected_content:
        test_fail(f"{case_name} content mismatch: got {actual!r}, expected {expected_content!r}")
    expected_dst_file.unlink()


# Case 1: Deep destination under un-stat-able intermediate directory
# (next_slash != NULL when coalescing "emulated/0")
reset_test_dirs()
src1 = src / 'case1.txt'
src1.write_text('payload-case-1\n')
verify_fresh_transfer(
    "Case 1 (deep path under emulated/0/Documents)",
    src1,
    str(docs_dir) + '/',
    docs_dir / 'case1.txt',
    'payload-case-1\n',
)

# Case 2: Destination is the immediate child of the un-stat-able directory,
# with NO trailing slash (next_slash == NULL, is_last becomes 1)
reset_test_dirs()
src2 = src / 'case2.txt'
src2.write_text('payload-case-2\n')
verify_fresh_transfer(
    "Case 2 (leaf child emulated/0 without trailing slash)",
    src2,
    str(storage_root),
    storage_root / 'case2.txt',
    'payload-case-2\n',
)

# Case 3: Relative path from inside the destination (cd .../Documents && rsync ... ./)
reset_test_dirs()
src3 = src / 'case3.txt'
src3.write_text('payload-case-3\n')
verify_fresh_transfer(
    "Case 3 (relative ./ inside emulated/0/Documents)",
    src3,
    './',
    docs_dir / 'case3.txt',
    'payload-case-3\n',
    cwd=str(docs_dir),
)

# Case 4: Trusted symlink (/sdcard -> .../storage/emulated/0)
reset_test_dirs()
src4 = src / 'case4.txt'
src4.write_text('payload-case-4\n')
verify_fresh_transfer(
    "Case 4 (symlink to emulated/0)",
    src4,
    str(sdcard_link / 'Documents') + '/',
    docs_dir / 'case4.txt',
    'payload-case-4\n',
)

# Case 5: Negative test - non-existent child under "emulated" must still fail
reset_test_dirs()
src5 = src / 'case5.txt'
src5.write_text('payload-case-5\n')
missing_dst = base / 'storage' / 'emulated' / '999' / 'Documents'
proc = subprocess.run(
    rsync_argv('-I', '-i', str(src5), str(missing_dst) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
if proc.returncode == 0:
    test_fail("Case 5 unexpectedly succeeded on non-existent child directory")
if missing_dst.exists():
    test_fail("Case 5 unexpectedly created non-existent child directory")

# Case 6: --confine-root with --files-from traversing the un-stat-able
# directory into a symlink that escapes confine_root MUST coalesce
# "emulated/0" (not fail with ENOENT) and then fail with ELOOP.
reset_test_dirs()
outside_secret = src / 'outside_secret.txt'
outside_secret.write_text('secret-payload-6\n')
escape_link = storage_root / 'escape_link'
escape_link.symlink_to(src)
files_from_list = storage_root / 'files.lst'
files_from_list.write_text('escape_link/outside_secret.txt\n')

proc = subprocess.run(
    rsync_argv(
        '-I', '-i',
        f'--confine-root={storage_root}',
        f'--files-from={files_from_list}',
        str(storage_root),
        str(confined_dst) + '/',
    ),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
if proc.returncode == 0:
    test_fail("Case 6 (--confine-root --files-from symlink escape under emulated/0) unexpectedly succeeded")
if b'Too many levels of symbolic links' not in proc.stderr:
    test_fail(f"Case 6 did not fail with ELOOP as expected: {proc.stderr!r}")
if b'No such file or directory' in proc.stderr:
    test_fail(f"Case 6 failed with ENOENT instead of ELOOP: {proc.stderr!r}")
if (confined_dst / 'outside_secret.txt').exists():
    test_fail("Case 6 transferred outside_secret.txt across confine_root")

# Case 7: --confine-root with --log-file pointing into emulated/0 outside
# confine_root MUST succeed and perform both a fresh transfer and a fresh log write.
reset_test_dirs()
outside_log_file = storage_root / 'rsync_case7.log'
src7 = confined_src / 'case7.txt'
src7.write_text('payload-case-7\n')
verify_fresh_transfer(
    "Case 7 (--confine-root with --log-file under emulated/0)",
    src7,
    str(confined_dst) + '/',
    confined_dst / 'case7.txt',
    'payload-case-7\n',
    extra_args=(
        f'--confine-root={confined_root}',
        f'--log-file={outside_log_file}',
    ),
)
if not outside_log_file.is_file():
    test_fail("Case 7 did not create --log-file under emulated/0")
log_text = outside_log_file.read_text()
if 'case7.txt' not in log_text:
    test_fail(f"Case 7 --log-file did not record the transfer of case7.txt: {log_text!r}")

# Case 8: Daemon mode with global 'log file' in the main section of rsyncd.conf
# pointing into emulated/0 while the module itself is confined to confined_dst.
reset_test_dirs()
daemon_emulated_log = storage_root / 'daemon_case8.log'
conf = write_daemon_conf([
    ('confined_mod', {
        'path': str(confined_dst),
        'read only': 'no',
        'use chroot': 'no',
    }),
], name='unstatable-emulated.conf')

# Place 'log file' and 'transfer logging' in the main (global) section of rsyncd.conf
conf_text = conf.read_text()
if re.search(r'^log file\s*=', conf_text, flags=re.M):
    conf_text = re.sub(
        r'^log file\s*=.*$',
        f'log file = {daemon_emulated_log}\ntransfer logging = yes',
        conf_text,
        count=1,
        flags=re.M,
    )
else:
    conf_text = f'log file = {daemon_emulated_log}\ntransfer logging = yes\n' + conf_text
conf.write_text(conf_text)

url = start_test_daemon(conf, DAEMON_PORT)

src8 = src / 'case8.txt'
src8.write_text('payload-case-8\n')
verify_fresh_transfer(
    "Case 8 (global daemon 'log file' under emulated/0 with confined_mod)",
    src8,
    f'{url}confined_mod/',
    confined_dst / 'case8.txt',
    'payload-case-8\n',
)
if not daemon_emulated_log.is_file():
    test_fail("Case 8 daemon did not create global 'log file' under emulated/0")
daemon_log_text = daemon_emulated_log.read_text()
if 'case8.txt' not in daemon_log_text:
    test_fail(f"Case 8 global daemon 'log file' did not record case8.txt transfer: {daemon_log_text!r}")
