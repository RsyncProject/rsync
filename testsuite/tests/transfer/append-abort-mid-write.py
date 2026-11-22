#!/usr/bin/env python3
# Hold SIGUSR1 until the receiver's first large write returns. Cleanup must
# leave a source prefix rather than flush the same buffer twice.

import os
import signal
import subprocess
import sys
import time

from harness.filesystem import unlink_if_exists
from harness.environment import RSYNC, SCRATCHDIR
from harness.filesystem import assert_same, make_data_file, makepath, rmtree
from harness.mutation import find_compiler
from harness.process import split_rsync_cmd
from harness.results import test_error, test_fail, test_skipped
from harness.rsync import rsync_argv, run_rsync
from harness import metadata

metadata(features={'linux_interpose', 'native_build'}, transports={'pipe'})


SHIM_C = r'''
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static ssize_t (*real_write)(int, const void *, size_t);
static char target_path[PATH_MAX];
static int held;

static void note(const char *env, const char *text)
{
	const char *path = getenv(env);
	int fd;

	if (!path || !*path)
		return;
	fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
	if (fd >= 0) {
		(void)real_write(fd, text, strlen(text));
		(void)close(fd);
	}
}

__attribute__((constructor))
static void init(void)
{
	const char *target = getenv("RSYNC_MIDWRITE_TARGET");

	real_write = (ssize_t (*)(int, const void *, size_t))
		dlsym(RTLD_NEXT, "write");
	if (target && *target && !realpath(target, target_path)) {
		strncpy(target_path, target, sizeof target_path - 1);
		target_path[sizeof target_path - 1] = '\0';
	}
	note("RSYNC_MIDWRITE_LOADED", "loaded\n");
}

static int fd_is_target(int fd)
{
	char procpath[64];
	char actual[PATH_MAX];
	ssize_t n;

	if (!target_path[0])
		return 0;
	snprintf(procpath, sizeof procpath, "/proc/self/fd/%d", fd);
	n = readlink(procpath, actual, sizeof actual - 1);
	if (n < 0)
		return 0;
	actual[n] = '\0';
	return strcmp(actual, target_path) == 0;
}

/* Report our pid, then wait (at most a minute) for the test to release us. */
static void hold(void)
{
	const char *release = getenv("RSYNC_MIDWRITE_RELEASE");
	struct timespec tick = { 0, 10 * 1000 * 1000 };
	char pid[32];
	int i;

	snprintf(pid, sizeof pid, "%d\n", (int)getpid());
	note("RSYNC_MIDWRITE_HELD", pid);
	for (i = 0; release && i < 6000 && access(release, F_OK) != 0; i++)
		nanosleep(&tick, NULL);
}

ssize_t write(int fd, const void *buf, size_t count)
{
	sigset_t all, old;
	ssize_t ret;
	int saved_errno;

	if (!real_write) {
		errno = EIO;
		return -1;
	}
	if (held || count < 65536 || !fd_is_target(fd))
		return real_write(fd, buf, count);

	held = 1;
	sigfillset(&all);
	sigprocmask(SIG_BLOCK, &all, &old);
	ret = real_write(fd, buf, count);
	saved_errno = errno;
	hold();
	sigprocmask(SIG_SETMASK, &old, NULL);
	errno = saved_errno;
	return ret;
}
'''


def build_shim():
    if not sys.platform.startswith('linux'):
        test_skipped('the mid-write hook is an LD_PRELOAD shim that matches '
                     'the destination fd through /proc/self/fd', capability='linux_interpose')
    cc = find_compiler()
    if not cc:
        test_skipped('no C compiler available to build the mid-write shim', capability='native_build')

    cfile = SCRATCHDIR / 'midwrite.c'
    sofile = SCRATCHDIR / 'midwrite.so'
    cfile.write_text(SHIM_C)
    proc = subprocess.run(
        cc + ['-shared', '-fPIC', '-O2', '-Wall', '-Wextra',
              '-o', str(sofile), str(cfile), '-ldl'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0 or not sofile.is_file():
        test_error('could not build the mid-write shim:\n' + (proc.stdout or '').strip())
    return sofile


def gone(pid):
    """True once `pid` has exited (a zombie counts: it can no longer write)."""
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()[0] == 'Z'
    except FileNotFoundError:
        return True


def first_difference(a, b):
    for i in range(0, min(len(a), len(b)), 4096):
        if a[i:i + 4096] != b[i:i + 4096]:
            return next(j for j in range(i, i + 4096) if a[j] != b[j])
    return min(len(a), len(b))


def abort_mid_write(mode):
    """Abort an rsync `mode` transfer while the receiver is inside its first
    write() to the destination, then check what that left behind."""
    base = SCRATCHDIR / 'append-abort'
    src = base / 'src'
    dest = base / 'dest'
    rmtree(base)
    makepath(src, dest)

    # A growing file whose first MiB was synced by an earlier run.
    make_data_file(src / 'grow.bin', 4 * 1024 * 1024)
    data = (src / 'grow.bin').read_bytes()
    prefix = 1024 * 1024
    (dest / 'grow.bin').write_bytes(data[:prefix])

    loaded = SCRATCHDIR / 'midwrite.loaded'
    held = SCRATCHDIR / 'midwrite.held'
    release = SCRATCHDIR / 'midwrite.release'
    for p in (loaded, held, release):
        unlink_if_exists(p)
    env = os.environ.copy()
    env['LD_PRELOAD'] = str(shim)
    env['RSYNC_MIDWRITE_TARGET'] = str((dest / 'grow.bin').resolve())
    env['RSYNC_MIDWRITE_LOADED'] = str(loaded)
    env['RSYNC_MIDWRITE_HELD'] = str(held)
    env['RSYNC_MIDWRITE_RELEASE'] = str(release)

    argv = rsync_argv('-a', mode, f'{src}/', f'{dest}/')
    print('Running:', ' '.join(argv))
    proc = subprocess.Popen(argv, env=env)
    try:
        deadline = time.monotonic() + 60
        while not held.is_file() or not held.read_text().endswith('\n'):
            if proc.poll() is not None or time.monotonic() > deadline:
                break
            time.sleep(0.01)
        if not loaded.exists():
            test_skipped('mid-write shim was not loaded; rsync may be static '
                         'or the platform ignored LD_PRELOAD', capability='linux_interpose')
        if not held.is_file():
            test_fail(f'{mode}: the receiver never wrote to the destination '
                      'file, so the shim could not hold it')
        receiver = int(held.read_text())
        # What the generator's exit cleanup does when a transfer aborts.
        os.kill(receiver, signal.SIGUSR1)
    finally:
        release.touch()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        test_fail(f'{mode}: rsync did not exit after its receiver was aborted')
    deadline = time.monotonic() + 30
    while not gone(receiver):
        if time.monotonic() > deadline:
            test_fail(f'{mode}: the receiver did not exit after SIGUSR1')
        time.sleep(0.01)
    if proc.returncode == 0:
        test_fail(f'{mode}: rsync exited 0 although its receiver was aborted')

    got = (dest / 'grow.bin').read_bytes()
    if len(got) > len(data) or got != data[:len(got)]:
        test_fail(f'{mode}: after the abort the destination ({len(got)} '
                  f'bytes) is not a prefix of the source; it differs at byte '
                  f'{first_difference(got, data)}, so the receiver wrote data '
                  'it had already written')

    run_rsync('-a', mode, f'{src}/', f'{dest}/')
    assert_same(src / 'grow.bin', dest / 'grow.bin',
                label=f'{mode} rerun after the abort')


if os.path.basename(split_rsync_cmd(RSYNC)[0]) == 'valgrind':
    test_skipped('the LD_PRELOAD write hook is not meaningful under valgrind', capability='linux_interpose')

shim = build_shim()
for mode in ('--append', '--inplace'):
    abort_mid_write(mode)

print('append-abort-mid-write: an abort in the middle of a write left the '
      'destination a clean prefix for --append and --inplace')
