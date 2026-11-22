#!/usr/bin/env python3

import json
from pathlib import Path
import subprocess

from harness import TestContext, requires, run
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.interoperability import endpoint_identity, seed, snapshot, update
from harness.results import test_fail
from harness.rsync import rsync_argv, rsync_command_binary


@requires(transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'filesystem', 'environment'}, tags={'interoperability', 'version-mix'})
def test(context: TestContext):
    seed(context.source)
    context.destination.mkdir()
    conf = write_daemon_conf([('data', {'path': str(context.destination), 'read only': 'no', 'munge symlinks': 'no'})])
    url = start_test_daemon(conf, 8743) + 'data/'
    identity = endpoint_identity(Path(rsync_command_binary()))
    checks = []

    def copy(name, source, destination, expected, actual, *options):
        subprocess.run(rsync_argv('-rltpH', *options, source, destination), check=True, capture_output=True, timeout=30)
        wanted, received = snapshot(expected), snapshot(actual)
        if wanted != received:
            print(json.dumps(dict(expected=wanted, received=received), sort_keys=True))
            test_fail(f'{name}: fixture contents or metadata differ')
        checks.append(name)

    copy('push', str(context.source) + '/', url, context.source, context.destination)
    update(context.source)
    copy('checksum update', str(context.source) + '/', url, context.source, context.destination, '--checksum', '--delete')
    received = context.scratch / 'received'
    received.mkdir()
    copy('pull', url, str(received) + '/', context.destination, received)
    (received / 'extra').touch()
    copy('pull delete', url, str(received) + '/', context.destination, received, '--delete')
    print(json.dumps(dict(endpoint=identity, checks=checks, filesystem_device=context.source.stat().st_dev,
                         scope='native fixture; no cross-platform transfer')))


if __name__ == '__main__':
    run(test)
