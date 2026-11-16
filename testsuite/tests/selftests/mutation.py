#!/usr/bin/env python3

from contextlib import ExitStack, redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness import Exit, TestContext, requires, run
from harness import environment, mutation
from harness.results import test_fail


def build_fixture(context, name, text='before\n', makefile='CFLAGS=-O2\n',
                  status=0, output=True, executable=True, missing_make=False,
                  platform='linux', append_cflags=None):
    root = context.scratch / name
    source = root / 'source'
    source.mkdir(parents=True)
    (source / 'fixture.c').write_text(text)
    if makefile is not None:
        (source / 'Makefile').write_text(makefile)
    (source / 'rsync').write_text('stale binary\n')

    def build(command, cwd, **kwargs):
        work = Path(cwd)
        if (work / 'rsync').exists():
            test_fail('patched build retained a stale executable')
        if (work / 'fixture.c').read_text() != 'after\n':
            test_fail('patched build received the wrong source')
        if append_cflags and append_cflags not in (work / 'Makefile').read_text():
            test_fail('patched build lost its extra compiler flags')
        if output:
            (work / 'rsync').write_text('built binary\n')
        return SimpleNamespace(returncode=status, stdout='fixture compiler output\n')

    def which(command):
        return None if missing_make and command == 'make' else '/fixture/' + command

    with ExitStack() as stack:
        stack.enter_context(patch.object(environment, 'SCRATCHDIR', root))
        stack.enter_context(patch.object(environment, 'SRCDIR', source))
        stack.enter_context(patch.object(environment, 'TOOLDIR', source))
        stack.enter_context(patch.object(mutation.sys, 'platform', platform))
        stack.enter_context(patch.object(mutation.platform, 'system', return_value='Linux'))
        stack.enter_context(patch.object(mutation.shutil, 'which', side_effect=which))
        stack.enter_context(patch.object(mutation.os, 'access', return_value=executable))
        stack.enter_context(patch.object(mutation.subprocess, 'run', side_effect=build))
        stack.enter_context(patch.object(mutation, 'test_skipped', side_effect=SystemExit(Exit.SKIP)))
        return mutation.build_patched_rsync(
            'patched', [('fixture.c', 'before\n', 'after\n')], append_cflags=append_cflags)


def require_exit(action, expected, reason=''):
    output = StringIO()
    status = None
    with redirect_stderr(output):
        try:
            action()
        except SystemExit as error:
            status = error.code
    if status != expected or reason not in output.getvalue():
        test_fail(f'patched build returned {status}, expected {expected}: {output.getvalue()}')


@requires(transports={'pipe'}, mutates={'filesystem'}, tags={'harness'})
def test(context: TestContext):
    for name, options, reason in (
            ('missing-target', {'text': 'different\n'}, 'patch target'),
            ('ambiguous-target', {'text': 'before\nbefore\n'}, 'patch target'),
            ('missing-flags', {'makefile': 'CPPFLAGS=-DTEST\n', 'append_cflags': '-fwrapv'}, 'CFLAGS'),
            ('failed-build', {'status': 7}, 'patched rsync build failed'),
            ('missing-output', {'output': False}, 'patched rsync build failed'),
            ('nonexecutable-output', {'executable': False}, 'patched rsync build failed')):
        require_exit(lambda: build_fixture(context, name, **options), Exit.FAIL, reason)

    for name, options in (
            ('no-makefile', {'makefile': None}),
            ('no-make', {'missing_make': True}),
            ('cygwin', {'platform': 'cygwin'})):
        require_exit(lambda: build_fixture(context, name, **options), Exit.SKIP)

    for name, options in (('built', {}), ('extra-flags', {'append_cflags': '-fwrapv'})):
        result = build_fixture(context, name, **options)
        if result.read_text() != 'built binary\n':
            test_fail('patched build did not return the newly built executable')


if __name__ == '__main__':
    run(test)
