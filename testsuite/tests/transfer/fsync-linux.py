#!/usr/bin/env python3

import os
import shlex
import shutil
import subprocess

from harness import metadata
from harness.environment import FROMDIR, RSYNC, SCRATCHDIR, TODIR
from harness.filesystem import assert_same, make_data_file, rmtree
from harness.process import split_rsync_cmd
from harness.results import test_fail, test_skipped

metadata(features={'linux_interpose'}, transports={'pipe'})

compiler = shlex.split(os.environ.get('CC') or '')
if not compiler:
    found = shutil.which('cc') or shutil.which('gcc')
    if not found:
        test_skipped('no C compiler is available', capability='linux_interpose')
    compiler = [found]
shim_source = SCRATCHDIR / 'fsync-shim.c'
shim = SCRATCHDIR / 'fsync-shim.so'
shim_source.write_text(r'''
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>

int fsync(int fd)
{
	static int (*real_fsync)(int);
	const char *marker = getenv("RSYNC_FSYNC_MARKER");
	int mark;

	if (!real_fsync)
		real_fsync = dlsym(RTLD_NEXT, "fsync");
	if (marker && *marker && (mark = open(marker, O_WRONLY | O_CREAT | O_APPEND, 0600)) >= 0) {
		(void)write(mark, "fsync\n", 6);
		(void)close(mark);
	}
	if (getenv("RSYNC_FSYNC_FAIL")) {
		errno = EIO;
		return -1;
	}
	return real_fsync(fd);
}
''')
build = subprocess.run([*compiler, '-shared', '-fPIC', '-o', str(shim), str(shim_source), '-ldl'],
                       capture_output=True, text=True)
if build.returncode or not shim.is_file():
    test_skipped(f'cannot build fsync interposer: {build.stdout}{build.stderr}',
                 capability='linux_interpose')

rmtree(FROMDIR)
rmtree(TODIR)
FROMDIR.mkdir(parents=True)
TODIR.mkdir()
make_data_file(FROMDIR / 'file', 1024 * 1024)
binary = split_rsync_cmd(RSYNC)
base_env = {**os.environ, 'LD_PRELOAD': str(shim)}

def transfer(name, *options, fail=False):
    destination = TODIR / name
    marker = SCRATCHDIR / f'{name}.marker'
    env = {**base_env, 'RSYNC_FSYNC_MARKER': str(marker)}
    if fail:
        env['RSYNC_FSYNC_FAIL'] = '1'
    result = subprocess.run([*binary, '-a', *options, f'{FROMDIR}/', f'{destination}/'],
                            env=env, capture_output=True, text=True)
    return result, marker, destination

control, control_marker, _ = transfer('control')
if control.returncode:
    test_fail(f'fsync control transfer failed:\n{control.stdout}{control.stderr}')
if control_marker.exists():
    test_fail('ordinary transfer called fsync')

synced, marker, destination = transfer('synced', '--fsync')
if synced.returncode:
    test_fail(f'--fsync transfer failed:\n{synced.stdout}{synced.stderr}')
if not marker.is_file() or not marker.read_text():
    test_fail('--fsync did not call fsync')
assert_same(destination / 'file', FROMDIR / 'file', label='--fsync content')

failed, marker, _ = transfer('failed', '--fsync', fail=True)
if failed.returncode == 0 or not marker.is_file():
    test_fail(f'fsync failure was ignored:\n{failed.stdout}{failed.stderr}')

print('--fsync calls fsync and reports a failed flush')
