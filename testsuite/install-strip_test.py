#!/usr/bin/env python3

import filecmp
import os
import shlex
import subprocess

from rsyncfns import SCRATCHDIR, TOOLDIR, test_fail


make_vars = {}
for line in (TOOLDIR / 'Makefile').read_text().splitlines():
    name, separator, value = line.partition('=')
    if separator and name in ('EXEEXT', 'STRIP'):
        make_vars[name] = value.strip()

if not make_vars.get('STRIP'):
    test_fail('configured Makefile does not define STRIP')

rsync = TOOLDIR / f"rsync{make_vars.get('EXEEXT', '')}"
if not rsync.is_file():
    test_fail('cannot find the built rsync binary')

make = shlex.split(os.environ.get('MAKE', 'make'))
tools = SCRATCHDIR / 'tools'
tools.mkdir()

for strip_name in ('strip', 'aarch64-linux-gnu-strip'):
    destdir = SCRATCHDIR / 'roots' / strip_name
    strip_log = SCRATCHDIR / f'{strip_name}.log'
    strip = tools / strip_name
    strip.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >"$STRIP_LOG"\n')
    strip.chmod(0o755)

    env = os.environ.copy()
    env['STRIP_LOG'] = str(strip_log)
    proc = subprocess.run(
        [*make, f'DESTDIR={destdir}', 'bindir=/bin', 'mandir=/share/man',
         'with_rrsync=no', 'INSTALL_STRIP=', f'STRIP={strip}',
         'install-strip'],
        cwd=TOOLDIR, env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        test_fail(f'install-strip failed with {strip_name}:\n'
                  f'{proc.stdout}{proc.stderr}')

    installed = destdir / 'bin' / rsync.name
    if not installed.is_file():
        test_fail(f'install-strip did not install {installed}')
    if not strip_log.is_file():
        test_fail(f'install-strip did not call {strip_name}')
    if strip_log.read_text().splitlines() != [str(installed)]:
        test_fail(f'{strip_name} was not called with {installed}')
    if not filecmp.cmp(rsync, installed, shallow=False):
        test_fail(f'{strip_name} unexpectedly changed the installed test binary')
