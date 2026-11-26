#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.interoperability import endpoint_identity, seed, snapshot, update
from harness.ssh import SSHServer, command


def check(binary, rrsync):
    checks = []
    # StrictModes checks every ancestor of AuthorizedKeysFile.
    with tempfile.TemporaryDirectory(prefix='rsync-ssh-', dir=pwd.getpwuid(os.geteuid()).pw_dir) as temporary:
        base = Path(temporary)
        restricted = base / 'restricted'
        restricted.mkdir()
        source, destination = base / 'source', restricted / 'incoming'
        seed(source)
        destination.mkdir()
        outside = base / 'outside'
        outside.mkdir()
        (outside / 'sentinel').write_text('outside transfer root\n')
        before = snapshot(outside)
        with SSHServer(base / 'ssh', restricted, binary, rrsync) as server:
            def transfer(name, source_arg, destination_arg, expected, received, *options):
                result = command([str(binary), '-rltpH', '--old-args', '--rsync-path=rsync',
                                  '-e', server.rsh(), *options, source_arg, destination_arg], server.env)
                if result.returncode:
                    log = (server.directory / 'server.log').read_text(errors='replace')
                    raise ValueError(f'{name}: rsync exited {result.returncode}: '
                                     + result.stderr.decode(errors='replace') + log)
                if snapshot(expected) != snapshot(received):
                    raise ValueError(f'{name}: contents or metadata differ')
                checks.append(name)

            transfer('push', str(source) + '/', server.remote('incoming/'), source, destination)
            update(source)
            transfer('checksum update', str(source) + '/', server.remote('incoming/'), source, destination,
                     '--checksum', '--delete')
            received = base / 'received'
            received.mkdir()
            transfer('pull', server.remote('incoming/'), str(received) + '/', destination, received)
            (received / 'extra').touch()
            transfer('pull delete', server.remote('incoming/'), str(received) + '/', destination, received, '--delete')

            result = command([*server.client(), f'{server.user}@{server.address}', 'printf escaped'], server.env)
            if result.returncode == 0 or b'SSH_ORIGINAL_COMMAND does not run rsync' not in result.stderr:
                raise ValueError('Restricted SSH accepted an arbitrary command or failed before rrsync')
            checks.append('command refusal')
            for direction in ('read', 'write'):
                denied = base / ('denied-' + direction)
                denied.mkdir()
                operands = ([server.remote('../outside/'), str(denied) + '/'] if direction == 'read'
                            else [str(source) + '/', server.remote('../outside/')])
                result = command([str(binary), '-rltpH', '--old-args', '--rsync-path=rsync',
                                  '-e', server.rsh(), *operands], server.env)
                if result.returncode == 0 or b'rrsync' not in result.stderr:
                    raise ValueError(f'Out-of-root {direction} was not refused by rrsync')
                if snapshot(outside) != before or any(denied.iterdir()):
                    raise ValueError(f'Out-of-root {direction} changed or disclosed data')
                checks.append('outside ' + direction + ' refusal')

            wrong = base / 'wrong-key'
            result = command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(wrong)], server.env)
            if result.returncode:
                raise ValueError('Wrong-host key generation failed')
            key = wrong.with_suffix('.pub').read_text().split()
            hosts = base / 'wrong-hosts'
            hosts.write_text(f'[{server.address}]:{server.port} {key[0]} {key[1]}\n')
            unchanged = snapshot(destination)
            result = command([str(binary), '-rltpH', '--old-args', '--rsync-path=rsync',
                              '-e', server.rsh(hosts), str(source) + '/', server.remote('incoming/')], server.env)
            if result.returncode == 0 or b'Host key verification failed' not in result.stderr:
                raise ValueError('Incorrect host key was not rejected')
            if snapshot(destination) != unchanged:
                raise ValueError('Host-key rejection changed destination data')
            checks.append('host key refusal')
        if server.active or server.thread.is_alive():
            raise ValueError('SSH fixture retained a session or listener')
    return dict(endpoint=endpoint_identity(binary), checks=checks, scope='local restricted SSH fixture')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rsync', type=Path, required=True)
    parser.add_argument('--rrsync', type=Path, required=True)
    args = parser.parse_args()

    def interrupted(signum, frame):
        # Python's I/O wrappers retry InterruptedError.
        raise ValueError('Restricted SSH check interrupted')

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = check(args.rsync.resolve(strict=True), args.rrsync.resolve(strict=True))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Restricted SSH check failed: {error}\n')
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
