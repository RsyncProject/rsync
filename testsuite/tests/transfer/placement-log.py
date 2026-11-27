#!/usr/bin/env python3

import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import forced_protocol, rsh_cmd, rsync_argv, rsync_path_arg, run_rsync


@requires(transports={'pipe'}, min_peer='4.0.0', mutates={'filesystem', 'process'},
          tags={'transfer', 'regression'})
def test(context: TestContext):
    context.source.mkdir()
    source = context.source / 'source-name'
    source.write_bytes(b'transferred content\n')

    for label, args in (('normal', ()), ('delayed', ('--delay-updates',)),
                        ('receiver-option', ('-M--omit-dir-times',))):
        target = context.scratch / label / 'destination-name'
        target.parent.mkdir()
        event = context.scratch / f'{label}.placements'
        run_rsync('-rt', *args, f'--placement-log={event}', str(source), str(target))
        if target.read_bytes() != source.read_bytes():
            test_fail(f'{label}: transfer did not place the file')
        if event.read_bytes() != target.name.encode('ascii') + b'\0':
            test_fail(f'{label}: expected one record naming the final file')

        same = context.scratch / f'{label}-unchanged.placements'
        run_rsync('-rt', *args, f'--placement-log={same}', str(source), str(target))
        if same.read_bytes():
            test_fail(f'{label}: an unchanged entry produced a placement')

    destination = context.destination
    destination.mkdir()
    event = context.scratch / 'pull.placements'
    run_rsync('-rt', '-e', rsh_cmd(), f'--rsync-path={rsync_path_arg()}',
              f'--placement-log={event}', f'lh:{context.source}/', f'{destination}/')
    if (event.read_bytes() != b'source-name\0'
            or (destination / source.name).read_bytes() != source.read_bytes()):
        test_fail('the local pull receiver did not record the final path')

    dry = context.scratch / 'dry.placements'
    run_rsync('-rtn', f'--placement-log={dry}', f'{context.source}/', f'{destination}/')
    if dry.exists():
        test_fail('dry run created a placement log')

    # Protocols before 31 cannot forward the receiver's startup exit code.
    open_failures = (11, 12) if (forced_protocol() or 31) < 31 else (11,)
    for record in (event, destination / 'inside.placements'):
        before = event.read_bytes()
        result = run_rsync('-rt', f'--placement-log={record}', f'{context.source}/',
                           f'{destination}/', capture_output=True, check=False)
        if result.returncode not in open_failures or 'cannot create placement log' not in result.stderr:
            test_fail(f'invalid log path was not refused: {result.stderr}')
        if event.read_bytes() != before or (destination / 'inside.placements').exists():
            test_fail('failed log creation changed an existing log or destination')

    result = run_rsync('-rt', '--inplace', f'--placement-log={dry}', str(source),
                       f'{destination}/', capture_output=True, check=False)
    if result.returncode != 1 or dry.exists():
        test_fail('in-place output did not refuse the placement log')

    for receiver_args in ((), ('-M--no-old-args', '-M--secluded-args')):
        result = run_rsync('-rt', *receiver_args, '-M--inplace', f'--placement-log={dry}',
                           str(source), f'{destination}/', capture_output=True, check=False)
        if (result.returncode == 0 or '--placement-log requires confined' not in result.stderr
                or dry.exists()):
            test_fail(f'in-place receiver option did not refuse the placement log: {result.stderr}')

    for protected in ((), ('-s',)):
        result = subprocess.run(
            rsync_argv(f'--placement-log={dry}', '--server', *protected, '.', str(destination)),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 1 or '--placement-log requires' not in result.stderr or dry.exists():
            test_fail(f'server invocation did not refuse the placement log: {result.stderr}')


if __name__ == '__main__':
    run(test)
