#!/usr/bin/env python3
# Regression guard for a source file changing size during send_files().
#
# The sender hardening for source reads now opens content through the confined
# sender path instead of the old plain open.  A source file can still change
# after the file list recorded its original size and after send_files() fstat()s
# the opened fd.  That per-file read failure may be diagnosed as a partial
# transfer, but it must not abort the sender/receiver pair before unrelated
# later file-list entries are transferred.
#
# To avoid a timing race, this test runs rsync under a scratch-built LD_PRELOAD
# shim.  The shim watches the exact source fd via /proc/self/fd and truncates it
# immediately before the first read() from that fd.  This leaves the file-list
# and send_files() fstat() sizes at the original value, then deterministically
# exercises fileio.c's "file changed mid transfer" map_ptr() path.

import os
import shlex
import shutil
import subprocess
import sys

from rsyncfns import (
    RSYNC, SCRATCHDIR, assert_same, make_data_file, makepath, rmtree,
    rsync_argv, test_fail, test_skipped, split_rsync_cmd,
)


SHIM_C = r'''
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static char target_path[PATH_MAX];
static int triggered;
static int resolving;

__attribute__((constructor))
static void mark_loaded(void)
{
	const char *mark = getenv("RSYNC_SHRINK_LOAD_MARK");
	int fd;

	if (!mark || !*mark)
		return;
	fd = open(mark, O_WRONLY | O_CREAT | O_TRUNC, 0600);
	if (fd >= 0) {
		(void)write(fd, "loaded\n", 7);
		(void)close(fd);
	}
}

static void init_target(void)
{
	const char *env;

	if (target_path[0])
		return;
	env = getenv("RSYNC_SHRINK_TARGET");
	if (!env || !*env)
		return;
	if (!realpath(env, target_path)) {
		strncpy(target_path, env, sizeof target_path - 1);
		target_path[sizeof target_path - 1] = '\0';
	}
}

static int fd_is_target(int fd)
{
	char procpath[64];
	char actual[PATH_MAX];
	ssize_t n;

	init_target();
	if (!target_path[0])
		return 0;
	snprintf(procpath, sizeof procpath, "/proc/self/fd/%d", fd);
	n = readlink(procpath, actual, sizeof actual - 1);
	if (n < 0)
		return 0;
	actual[n] = '\0';
	return strcmp(actual, target_path) == 0;
}

ssize_t read(int fd, void *buf, size_t count)
{
	static ssize_t (*real_read)(int, void *, size_t);

	if (!real_read) {
		real_read = (ssize_t (*)(int, void *, size_t))dlsym(RTLD_NEXT, "read");
		if (!real_read) {
			errno = EIO;
			return -1;
		}
	}

	if (!triggered && !resolving) {
		resolving = 1;
		if (fd_is_target(fd)) {
			const char *mark = getenv("RSYNC_SHRINK_MARK");
			triggered = 1;
			if (truncate(target_path, 0) == 0 && mark && *mark) {
				int mfd = open(mark, O_WRONLY | O_CREAT | O_TRUNC, 0600);
				if (mfd >= 0) {
					(void)write(mfd, "shrunk\n", 7);
					(void)close(mfd);
				}
			}
		}
		resolving = 0;
	}

	return real_read(fd, buf, count);
}
'''


def compiler():
    cc = os.environ.get('CC')
    if cc:
        return shlex.split(cc)
    for name in ('cc', 'gcc', 'clang'):
        path = shutil.which(name)
        if path:
            return [path]
    return None


def build_shim():
    if not sys.platform.startswith('linux'):
        test_skipped('source-size-change regression uses LD_PRELOAD and '
                     '/proc/self/fd to synchronise the source read')
    cc = compiler()
    if not cc:
        test_skipped('no C compiler available to build the source-read shim')

    cfile = SCRATCHDIR / 'source_shrink_read.c'
    sofile = SCRATCHDIR / 'source_shrink_read.so'
    cfile.write_text(SHIM_C)
    proc = subprocess.run(
        cc + ['-shared', '-fPIC', '-O2', '-Wall', '-Wextra',
              '-o', str(sofile), str(cfile), '-ldl'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0 or not sofile.is_file():
        test_skipped('could not build the source-read shim:\n'
                     + (proc.stdout or '').strip())
    return sofile


first = split_rsync_cmd(RSYNC)[0] if RSYNC else ''
if os.path.basename(first) == 'valgrind':
    test_skipped('LD_PRELOAD read hook is not meaningful under valgrind')

base = SCRATCHDIR / 'source-change-size'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
makepath(src, dest)

changing = src / '01-changing.bin'
later = src / '02-later.txt'
make_data_file(changing, 1024 * 1024)
later.write_text('later file must still transfer\n')

shim = build_shim()
mark = SCRATCHDIR / 'source_shrink_read.mark'
load_mark = SCRATCHDIR / 'source_shrink_load.mark'
mark.unlink(missing_ok=True)
load_mark.unlink(missing_ok=True)
env = os.environ.copy()
env['LD_PRELOAD'] = str(shim)
env['RSYNC_SHRINK_TARGET'] = str(changing.resolve())
env['RSYNC_SHRINK_MARK'] = str(mark)
env['RSYNC_SHRINK_LOAD_MARK'] = str(load_mark)

argv = rsync_argv('-a', '--no-whole-file', f'{src}/', f'{dest}/')
print('Running:', ' '.join(argv))
proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      text=True, env=env)
out = proc.stdout or ''
print(out, end='')

if not load_mark.exists():
    test_skipped('source-read shim was not exercised; rsync may be static or '
                 'the platform ignored LD_PRELOAD')
if not mark.exists():
    test_fail('source-read shim loaded but did not target the changing source '
              'file')

if proc.returncode == 0:
    test_fail('rsync succeeded even though the shim forced a source read error; '
              'expected a partial-transfer diagnosis')
if proc.returncode != 23:
    test_fail(f'expected partial-transfer exit 23 after the source changed '
              f'size; got {proc.returncode}:\n{out}')

if not (dest / later.name).is_file():
    test_fail('sender aborted before transferring the later file-list entry')
assert_same(later, dest / later.name, label='later file')

if 'read errors mapping' not in out:
    test_fail('rsync exited 23 but did not report the sender-side changed-source '
              'diagnosis:\n' + out)

print('source-change-size-continues: changed source file produced a partial '
      'transfer while a later file-list entry still transferred')
