#!/usr/bin/env python3

import os

from harness import TestContext, requires, run
from harness.comparison import checkit
from harness.rsync import rsh_cmd, rsync_path_arg

@requires(features={'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='3.0.0',
          tags={'compatibility', 'transfer', 'version-mix'})
def test(context: TestContext):
    source = context.source / 'secluded'
    destination = context.destination / 'secluded'
    source.mkdir(parents=True)
    context.destination.mkdir()
    os.environ['RSYNC_RSH'] = rsh_cmd()

    def transfer(label, source_host, destination_host):
        with (source / 'file').open('a') as stream:
            stream.write(label + '\n')
        checkit(['-ais', f'--rsync-path={rsync_path_arg()}',
                 f'{source_host}{source}/', f'{destination_host}{destination}'],
                context.source, context.destination)

    transfer('pull', 'lh:', '')
    transfer('push', '', 'lh:')

if __name__ == '__main__':
    run(test)
