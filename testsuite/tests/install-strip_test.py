#!/usr/bin/env python3

import filecmp
import os
import re
import shlex
import shutil
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, TOOLDIR, test_fail


make_vars = {}
for line in (TOOLDIR / 'Makefile').read_text().splitlines():
    name, separator, value = line.partition('=')
    if separator and name in ('prefix', 'exec_prefix', 'bindir',
                              'EXEEXT', 'STRIP'):
        make_vars[name] = value.strip()

if not make_vars.get('STRIP'):
    test_fail('configured Makefile does not define STRIP')

source_rsync = TOOLDIR / f"rsync{make_vars.get('EXEEXT', '')}"
if not source_rsync.is_file():
    test_fail('cannot find the built rsync binary')

builddir = SCRATCHDIR / 'build'
builddir.mkdir()
makefile_text = (TOOLDIR / 'Makefile').read_text()
if 'install: all\n' not in makefile_text:
    test_fail('cannot isolate the install target from the shared build')
if 'Makefile: Makefile.in config.status configure.sh config.h.in\n' not in makefile_text:
    test_fail('cannot isolate Makefile regeneration from the shared build')
(builddir / 'Makefile').write_text(
    makefile_text
    .replace('install: all\n', 'install:\n', 1)
    .replace('Makefile: Makefile.in config.status configure.sh config.h.in\n',
             'Makefile:\n', 1)
)

for name in (source_rsync.name, 'install-sh', 'rsync-ssl', 'rrsync', 'rsync.1',
             'rsync-ssl.1', 'rsyncd.conf.5', 'rrsync.1'):
    source = TOOLDIR / name
    if not source.is_file():
        source = SRCDIR / name
    if source.is_file():
        shutil.copy2(source, builddir / name)


def expand_make_value(value):
    for _ in range(10):
        expanded = re.sub(
            r'\$\{([^}]+)\}|\$\(([^)]+)\)',
            lambda match: make_vars.get(match.group(1) or match.group(2),
                                        match.group(0)),
            value,
        )
        if expanded == value:
            return expanded
        value = expanded
    test_fail(f'cannot expand Makefile value {value!r}')


bindir = expand_make_value(make_vars.get('bindir', ''))
if not bindir or '$' in bindir:
    test_fail(f'cannot determine configured bindir from {bindir!r}')

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
        [*make, f'DESTDIR={destdir}', f'STRIP={strip}', 'install-strip'],
        cwd=builddir, env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        test_fail(f'install-strip failed with {strip_name}:\n'
                  f'{proc.stdout}{proc.stderr}')

    installed = destdir / bindir.lstrip('/') / source_rsync.name
    if not installed.is_file():
        test_fail(f'install-strip did not install {installed}')
    if not strip_log.is_file():
        test_fail(f'install-strip did not call {strip_name}')
    if strip_log.read_text().splitlines() != [str(installed)]:
        test_fail(f'{strip_name} was not called with {installed}')
    if not filecmp.cmp(source_rsync, installed, shallow=False):
        test_fail(f'{strip_name} unexpectedly changed the installed test binary')
