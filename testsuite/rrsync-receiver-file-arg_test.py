#!/usr/bin/env python3
"""Receiver file arguments must remain beneath a pinned parent."""

import os
import shlex
import subprocess
import time

from rsyncfns import (
    SCRATCHDIR, makepath, patched_rrsync, proc_self_fd_pins, rmtree,
    rsync_argv, test_fail, test_skipped,
)

if not proc_self_fd_pins():
    test_skipped('requires /proc/self/fd directory pins')

base = SCRATCHDIR / 'rrsync-receiver-file'
rmtree(base)
restricted = base / 'restricted'
outside = base / 'outside'
source = base / 'source'
downloads = base / 'downloads'
makepath(restricted, outside, downloads)
source.write_text('replacement contents\n')

# The optional gate pauses the real server after rrsync validates its argument.
shim = base / 'rsync-shim'
shim.write_text('''#!/usr/bin/env python3
import os, pathlib, sys, time
ready = os.environ.get('RRSYNC_TEST_READY')
if ready:
    pathlib.Path(ready).touch()
    go = pathlib.Path(os.environ['RRSYNC_TEST_GO'])
    end = time.monotonic() + 10
    while not go.exists():
        if time.monotonic() >= end:
            sys.exit('timed out waiting for test gate')
        time.sleep(0.01)
cmd = %r
os.execvp(cmd[0], cmd + sys.argv[1:])
''' % rsync_argv())
shim.chmod(0o755)
rrsync = patched_rrsync(base, rsync_path=str(shim))
rsh = base / 'fake-rsh'
rsh.write_text('#!/bin/sh\nshift\nSSH_ORIGINAL_COMMAND="$*"\n'
               'export SSH_ORIGINAL_COMMAND\nexec %s -no-lock %s\n'
               % (shlex.quote(str(rrsync)), shlex.quote(str(restricted))))
rsh.chmod(0o755)


def command(src, dest, *options):
    return rsync_argv('-ac', '-e', str(rsh), *options, str(src), str(dest))


def transfer(src, dest, *options, succeeds=True):
    proc = subprocess.run(command(src, dest, *options), capture_output=True,
                          text=True, timeout=20)
    if (proc.returncode == 0) != succeeds:
        test_fail(f'transfer {src} -> {dest}: rc={proc.returncode}, '
                  f'{proc.stdout!r} {proc.stderr!r}')
    return proc


def check_contents(path):
    if path.is_symlink() or not path.is_file() or path.read_text() != source.read_text():
        test_fail(f'wrong file contents or type: {path}')


for option in ((), ('--inplace',)):
    destination = restricted / 'existing'
    destination.write_text('old\n')
    transfer(source, 'dummy:existing', *option)
    check_contents(destination)
    new = restricted / 'new'
    new.unlink(missing_ok=True)
    transfer(source, 'dummy:new', *option)
    check_contents(new)

makepath(restricted / 'directory')
transfer(source, 'dummy:directory/')
check_contents(restricted / 'directory' / source.name)
transfer('dummy:existing', str(downloads) + '/')
check_contents(downloads / 'existing')

marker = outside / 'untouched'
marker.write_text('outside fixture\n')
transfer(source, 'dummy:../outside/untouched', succeeds=False)
(restricted / 'outside-link').symlink_to(outside, target_is_directory=True)
transfer(source, 'dummy:outside-link/untouched', succeeds=False)
if marker.read_text() != 'outside fixture\n':
    test_fail('a rejected destination changed the outside fixture')


def gated_transfer(name, change):
    ready = base / 'ready'
    go = base / 'go'
    ready.unlink(missing_ok=True)
    go.unlink(missing_ok=True)
    env = {**os.environ, 'RRSYNC_TEST_READY': str(ready), 'RRSYNC_TEST_GO': str(go)}
    proc = subprocess.Popen(command(source, f'dummy:{name}', '--inplace'),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        end = time.monotonic() + 10
        while not ready.exists():
            if proc.poll() is not None or time.monotonic() >= end:
                test_fail('receiver did not reach the post-validation gate')
            time.sleep(0.01)
        change()
        go.touch()
        stdout, stderr = proc.communicate(timeout=20)
        if marker.read_text() != 'outside fixture\n':
            test_fail(f'in-place transfer changed the outside fixture: {stdout} {stderr}')
        return proc.returncode
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


leaf = restricted / 'raced-leaf'
leaf.write_text('old\n')


def replace_leaf():
    leaf.unlink()
    leaf.symlink_to(marker)


rc = gated_transfer('raced-leaf', replace_leaf)
if rc == 0:
    check_contents(leaf)

parent = restricted / 'raced-parent'
makepath(parent)
(parent / 'untouched').write_text('old\n')
saved_parent = restricted / 'saved-parent'


def replace_parent():
    parent.rename(saved_parent)
    parent.symlink_to(outside, target_is_directory=True)


if gated_transfer('raced-parent/untouched', replace_parent) == 0:
    check_contents(saved_parent / 'untouched')

print('rrsync receiver file overwrite, creation, directory, pull and path checks passed')
