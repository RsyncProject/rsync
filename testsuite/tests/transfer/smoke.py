#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.rsync import checkit, rsync_argv, rsync_path_arg, rsh_cmd, run_rsync, test_fail

@requires(features={'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'environment', 'filesystem'}, tags={'compatibility', 'smoke', 'transfer', 'version-mix'})
def test(context: TestContext):
    source_root = context.source
    destination = context.destination
    os.environ['RSYNC_RSH'] = rsh_cmd()
    for option in ('--version', '--info=help', '--debug=help'):
        if run_rsync(option, check=False).returncode:
            test_fail(f'{option} failed')
    daemon_help = run_rsync('--daemon', '--help', capture_output=True).stdout
    if '--no-detach' not in daemon_help or 'rsync --daemon' not in daemon_help:
        test_fail('--daemon --help did not print daemon usage')

    name = 'A weird)name'
    source = source_root / name
    source.mkdir(parents=True)

    def append(text):
        with open(source / 'file', 'a') as stream:
            stream.write(text + '\n')

    def copy(options, source_host, destination_host):
        checkit([*options, f'--rsync-path={rsync_path_arg()}', f'{source_host}{source}/',
                 f'{destination_host}{destination / name}'], source_root, destination)

    append('local')
    checkit(['-ai', f'{source_root}/', f'{destination}/'], source_root, destination)
    for text, options, source_host, destination_host in (
        ('pull', ['-ai'], 'lh:', ''), ('push', ['-ai'], '', 'lh:'),
        ('secluded-pull', ['-ais'], 'lh:', ''), ('secluded-push', ['-ais'], '', 'lh:'),
    ):
        append(text)
        copy(options, source_host, destination_host)

    for name in ('one', 'two'):
        (source_root / name).touch()
    for options, overrides in ((('--old-args',), {}), ((), {'RSYNC_OLD_ARGS': '1'})):
        for name in ('one', 'two'):
            (destination / name).unlink(missing_ok=True)
        env = os.environ.copy()
        env.update(overrides)
        subprocess.run(rsync_argv('-ai', *options, f'--rsync-path={rsync_path_arg()}',
                                 'lh:one two', f'{destination}/'), cwd=source_root, env=env, check=True)
        if not all((destination / name).is_file() for name in ('one', 'two')):
            test_fail('old argument handling failed')

if __name__ == '__main__':
    run(test)
