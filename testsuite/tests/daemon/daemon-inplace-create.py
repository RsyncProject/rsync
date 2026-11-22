#!/usr/bin/env python3

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.filesystem import assert_same, makepath
from harness.rsync import run_rsync


@requires(transports={'pipe', 'tcp'}, min_peer={'primary': '2.6.0', 'secondary': '3.5.0'},
          mutates={'environment', 'filesystem'}, tags={'regression', 'transfer', 'version-mix'})
def test(context: TestContext):
    source = context.source
    module = context.destination
    makepath(source / 'nested', module / 'nested')
    files = [('top', b'new file\n'), ('nested/leaf', b'new file in an existing directory\n')]
    for name, content in files:
        (source / name).write_bytes(content)

    config = write_daemon_conf([
        ('upload', {'path': str(module), 'read only': 'no', 'use chroot': 'no'}),
    ])
    url = start_test_daemon(config, 12942)
    run_rsync('-r', '--inplace', f'{source}/', f'{url}upload/')

    for name, _ in files:
        assert_same(source / name, module / name, label=name)


if __name__ == '__main__':
    run(test)
