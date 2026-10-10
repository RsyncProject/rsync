#!/usr/bin/env python3

from contextlib import redirect_stderr
from io import StringIO
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import patch

from harness import daemon
from harness import (Exit, TestContext, forced_protocol, requires, rsh_cmd, rsync_argv,
                     rsync_argv_for, rsync_command_binary, rsync_path_arg, rsync_supports, run,
                     run_rsync, split_rsync_cmd, test_fail, under_valgrind)

@requires(transports={'pipe'}, tags={'harness'})
def test(context: TestContext):
    bindir = context.scratch / 'bin with spaces'
    bindir.mkdir()
    binary = bindir / 'rsync'
    binary.write_text('')
    command = f'{binary} --protocol=31'
    if split_rsync_cmd(command) != [str(binary), '--protocol=31']:
        test_fail('an unquoted executable path containing spaces was split')
    if rsync_command_binary(command) != str(binary):
        test_fail('the rsync executable was not found in its command')
    if forced_protocol(command) != 31 or forced_protocol(str(binary)) is not None:
        test_fail('the forced protocol was parsed incorrectly')
    protocol_dir = context.scratch / '--protocol=99'
    protocol_dir.mkdir()
    protocol_binary = protocol_dir / 'rsync'
    protocol_binary.write_text('')
    if forced_protocol(str(protocol_binary)) is not None:
        test_fail('a protocol-like path was parsed as an option')

    wrapped = f'valgrind --tool=memcheck {shlex.quote(str(binary))}'
    wrapped_argv = ['valgrind', '--tool=memcheck', str(binary)]
    if split_rsync_cmd(wrapped) != wrapped_argv or not under_valgrind(wrapped):
        test_fail('a wrapped rsync command was not preserved')
    if under_valgrind(str(bindir / 'valgrind-rsync')):
        test_fail('valgrind was inferred from an executable path')
    if rsync_path_arg(wrapped) != shlex.join(wrapped_argv):
        test_fail('the remote rsync command was not quoted')
    if rsync_argv(command, '-a') != [str(binary), '--protocol=31', '-a']:
        test_fail('rsync arguments were not appended')

    replacement = bindir / 'peer'
    replacement.write_text('')
    if rsync_argv_for(wrapped, replacement, '-a') != [
            'valgrind', '--tool=memcheck', str(replacement), '-a']:
        test_fail('the wrapped rsync executable was not replaced')

    source = context.scratch / 'source with spaces'
    shell = str(source / 'support' / 'lsh.sh')
    expected_rsh = shlex.join([shell, '--option', 'value with spaces'])
    if rsh_cmd(shell, '--option', 'value with spaces') != expected_rsh:
        test_fail('the remote shell command was not quoted')

    unsupported = context.scratch / 'unsupported.py'
    unsupported.write_text("import sys\nsys.stderr.write('unknown option\\n')\nraise SystemExit(1)\n")
    supported = context.scratch / 'supported.py'
    supported.write_text('raise SystemExit(0)\n')
    unsupported_command = f'{sys.executable} {unsupported}'
    supported_command = f'{sys.executable} {supported}'
    if rsync_supports(unsupported_command, '--same-flag'):
        test_fail('an unsupported option was accepted')
    if not rsync_supports(supported_command, '--same-flag'):
        test_fail('feature-probe caching ignored the changed command')
    if run_rsync(supported_command, capture_output=True).returncode != 0:
        test_fail('a successful command failed')
    if run_rsync(unsupported_command, check=False, capture_output=True).returncode != 1:
        test_fail('a failed command result was not returned')

    with patch.object(daemon.os, 'kill'), patch.object(daemon.subprocess, 'run') as run_ps:
        run_ps.return_value = SimpleNamespace(returncode=0, stdout='notrsync\n')
        if daemon._is_rsync(123):
            test_fail('daemon cleanup accepted a process name containing rsync')
        run_ps.return_value = SimpleNamespace(returncode=0, stdout='rsync\n')
        if not daemon._is_rsync(123):
            test_fail('daemon cleanup rejected the configured rsync process name')

    error_output = StringIO()
    try:
        with redirect_stderr(error_output):
            run_rsync(unsupported_command, capture_output=True)
    except SystemExit as error:
        if error.code != Exit.FAIL or 'rsync exited 1:' not in error_output.getvalue():
            test_fail('a failed command reported the wrong result')
    else:
        test_fail('a failed command did not stop the test')

if __name__ == '__main__':
    run(test)
