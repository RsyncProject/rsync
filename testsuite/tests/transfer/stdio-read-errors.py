#!/usr/bin/env python3

import errno
import os
import shlex
import signal
import subprocess
import sys

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.environment import FROMDIR, SCRATCHDIR
from harness.results import test_fail
from harness.rsync import rsync_argv


@requires(features={'linux_interpose'}, transports={'pipe'}, mutates={'filesystem', 'process', 'socket'}, tags={'daemon', 'io'})
def test(context: TestContext):
    library = SCRATCHDIR / 'stdio-error.so'
    fixture = context.repository / 'testsuite/fixtures/stdio-error.c'
    build = subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture), '-ldl'], capture_output=True, text=True)
    if build.returncode:
        test_fail(f'cannot build stdio hook: {build.stderr}')
    FROMDIR.mkdir()
    (FROMDIR / 'keep').write_text('keep\n')
    (FROMDIR / 'drop').write_text('drop\n')
    config = SCRATCHDIR / 'rsyncd.conf'
    config_text = f'use chroot = no\n[files]\npath = {FROMDIR}\n'
    config.write_text(config_text)
    url = start_test_daemon(config, 12918)
    marker = SCRATCHDIR / 'read-error'

    def invoke(name, arguments, path, after, mode='eio', success=False):
        marker.unlink(missing_ok=True)
        env = {**os.environ, 'LD_PRELOAD': str(library), 'RSYNC_TEST_READ_PATH': str(path),
               'RSYNC_TEST_READ_AFTER': str(after), 'RSYNC_TEST_READ_ERROR': mode,
               'RSYNC_TEST_READ_MARKER': str(marker)}
        with subprocess.Popen(rsync_argv(*arguments), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, start_new_session=True) as process:
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate(timeout=5)
                test_fail(f'{name}: stalled after a read error: {output}')
        if after >= 0 and not marker.exists():
            test_fail(f'{name}: read hook did not fire: {output}')
        if process.returncode < 0:
            test_fail(f'{name}: terminated by signal {-process.returncode}: {output}')
        if (process.returncode == 0) != success:
            test_fail(f'{name}: unexpected exit {process.returncode}: {output}')
        return output

    invoke('config control', [url], config, -1, success=True)
    for after in (0, len('use chroot = no\n'), config_text.index('[files]') + 3, len(config_text) - 3, len(config_text)):
        invoke('config error', [url], config, after)
    invoke('config fdopen error', [url], config, 0, mode='fdopen')
    output = invoke('interrupted config', [url], config, config_text.index('[files]') + 3, mode='eintr', success=True)
    if 'files' not in output:
        test_fail('interrupted config lost the module name')
    config.write_text('')
    invoke('empty config', [url], config, -1, success=True)
    config.write_text('# comment without a newline')
    invoke('config comment', [url], config, -1, success=True)
    config.write_text(config_text.rstrip('\n'))
    invoke('config without newline', [url], config, -1, success=True)
    included = SCRATCHDIR / 'included.conf'
    included.write_text(f'[included]\npath = {FROMDIR}\n')
    config.write_text(f'use chroot = no\n&include {included}\n' + config_text)
    invoke('included config control', [url], included, -1, success=True)
    invoke('included config error', [url], included, 3)

    motd = SCRATCHDIR / 'motd'
    motd.write_text('message of the day\n')
    config.write_text(f'motd file = {motd}\n' + config_text)
    invoke('motd control', [url], motd, -1, success=True)
    output = invoke('interrupted motd', [url], motd, 4, mode='eintr', success=True)
    if motd.read_text() not in output:
        test_fail('interrupted MOTD lost data')
    invoke('motd error', [url], motd, 4)
    invoke('motd fdopen error', [url], motd, 0, mode='fdopen')
    motd.write_text('message of the day\n' * 1024)
    output = invoke('interrupted long motd', [url], motd, 4099, mode='eintr', success=True)
    if motd.read_text() not in output:
        test_fail('interrupted MOTD lost data across buffers')

    early = SCRATCHDIR / 'early-input'
    early.write_bytes(bytes(range(256)))
    captured = SCRATCHDIR / 'early-received'
    hook = shlex.join([sys.executable, '-S', '-c',
                      f'import sys; from pathlib import Path; Path({str(captured)!r}).write_bytes(sys.stdin.buffer.read())'])
    config.write_text(config_text + f'early exec = {hook}\n')
    arguments = [f'--early-input={early}', f'{url}files/']
    invoke('early-input control', arguments, early, -1, success=True)
    invoke('interrupted early-input', arguments, early, 4, mode='eintr', success=True)
    if captured.read_bytes() != early.read_bytes():
        test_fail('interrupted early-input lost or duplicated data')
    invoke('early-input error', arguments, early, 4)
    output = invoke('early-input fdopen error', arguments, early, 0, mode='fdopen')
    if os.strerror(errno.EMFILE) not in output:
        test_fail(f'early-input fdopen error was overwritten: {output}')
    invoke('early-input short file', arguments, early, 4, mode='eof')
    early.write_text('')
    invoke('empty early-input', arguments, early, -1, success=True)

    rules = SCRATCHDIR / 'rules'
    for ending in ('\n', '\r\n'):
        rules.write_bytes(f'- drop{ending}'.encode())
        destination = SCRATCHDIR / f'destination-{len(ending)}'
        arguments = ['-r', f'--exclude-from={rules}', f'{FROMDIR}/', f'{destination}/']
        invoke('filter control', arguments, rules, -1, success=True)
        if not (destination / 'keep').exists() or (destination / 'drop').exists():
            test_fail('filter control copied the wrong files')
        invoke('filter error', arguments, rules, len('- drop') + (ending == '\r\n'))
        output = invoke('filter fdopen error', arguments, rules, 0, mode='fdopen')
        if os.strerror(errno.EMFILE) not in output:
            test_fail(f'filter fdopen error was overwritten: {output}')
        invoke('interrupted filter', arguments, rules, 3, mode='eintr', success=True)
        if (destination / 'drop').exists():
            test_fail('interrupted filter lost a rule')

    protected = SCRATCHDIR / 'protected-destination'
    protected.mkdir()
    (protected / 'protected').write_text('retained\n')
    rules.write_text('- protected\n')
    arguments = ['-r', '--delete', f'--exclude-from={rules}', f'{FROMDIR}/', f'{protected}/']
    invoke('filter deletion guard', arguments, rules, 3)
    if (protected / 'protected').read_text() != 'retained\n':
        test_fail('filter read error allowed deletion')

    nested = SCRATCHDIR / 'private-merge-name'
    nested.write_text('- drop\n')
    rules.write_text(f'. {nested}\n')
    arguments = ['-r', '-f', f'. {rules}', f'{FROMDIR}/', f'{protected}/']
    output = invoke('nested filter error', arguments, nested, 3)
    if nested.name in output:
        test_fail('nested filter error exposed a filename from file contents')


if __name__ == '__main__':
    run(test)
