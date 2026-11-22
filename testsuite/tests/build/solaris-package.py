#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail


def make_tool(directory, name, body):
    path = directory / name
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o755)


def package(context, tools, name, *, fail_build=False, existing_stage=False):
    source = context.scratch / name
    work = source / 'packaging/solaris/5.8'
    work.mkdir(parents=True)
    for artifact in ('rsync', 'rsync.1', 'rsyncd.conf.5', 'README.md', 'COPYING', 'tech_report.pdf'):
        (source / artifact).write_text(f'fixture {artifact}\n')
    stage = work / 'SMBrsync'
    if existing_stage:
        stage.mkdir()
        (stage / 'existing').write_text('preserve this file\n')

    trace = source / 'package.trace'
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ.get('PATH', ''),
           'PACKAGE_TRACE': str(trace), 'PACKAGE_STAGE': str(stage.resolve()),
           'PACKAGE_FAIL_BUILD': 'yes' if fail_build else 'no'}
    script = context.repository / 'packaging/solaris/build_pkg.sh'
    result = subprocess.run(['sh', str(script)], cwd=work, env=env,
                            capture_output=True, text=True, timeout=20)
    events = trace.read_text().splitlines() if trace.exists() else []
    return result, work, stage, events


@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'build'})
def test(context: TestContext):
    tools = context.scratch / 'bin'
    tools.mkdir()
    make_tool(tools, 'pkgmk', 'printf "%s\\n" pkgmk >> "$PACKAGE_TRACE"\n'
              '[ "$(pwd -P)" = "$PACKAGE_STAGE" ] || exit 7\n'
              '[ -f pkginfo ] && [ -f prototype ] || exit 8\n'
              '[ "$PACKAGE_FAIL_BUILD" = no ]\n')
    make_tool(tools, 'pkgtrans', 'printf "%s\\n" pkgtrans >> "$PACKAGE_TRACE"\n'
              '[ "$(pwd -P)" = "$PACKAGE_STAGE" ] || exit 7\n'
              'printf "fixture package\\n" > "$3"\n')
    # Record the cleanup target without removing any files on either revision.
    make_tool(tools, 'rm', 'printf "%s\\n" cleanup >> "$PACKAGE_TRACE"\n'
              '[ "$#" -eq 2 ] && [ "$1" = -rf ] && [ "$(cd "$2" && pwd -P)" = "$PACKAGE_STAGE" ]\n')

    result, work, _, events = package(context, tools, 'checkout with spaces')
    packages = list(work.glob('*.pkg'))
    if (result.returncode or events != ['pkgmk', 'pkgtrans', 'cleanup'] or len(packages) != 1
            or packages[0].read_text() != 'fixture package\n'):
        test_fail(f'package build in a path with spaces failed: {events!r}\n{result.stderr}')

    result, work, stage, events = package(context, tools, 'failed', fail_build=True)
    if not result.returncode or events != ['pkgmk'] or list(work.glob('*.pkg')) or not stage.is_dir():
        test_fail(f'package build continued after pkgmk failed: {events!r}\n{result.stderr}')

    result, _, stage, events = package(context, tools, 'existing', existing_stage=True)
    if not result.returncode or events or (stage / 'existing').read_text() != 'preserve this file\n':
        test_fail(f'package build reused an existing stage: {events!r}\n{result.stderr}')


if __name__ == '__main__':
    run(test)
