import argparse
from contextlib import ExitStack
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'testsuite'))
from harness.interoperability import PLATFORMS, endpoint_identity, seed, snapshot, transfer_cases, update
from harness.interop_metadata import (FEATURES, metadata_cases, metadata_filesystem, metadata_snapshot,
                                     qualify, seed_metadata, supported)
from harness import xattrs
from harness.process import quote_command
from harness.ssh import ProcessServer, SSHServer, client_command, command, public_key

SSH_PORT, DAEMON_PORT = 18731, 18733


def name(case):
    if tuple(case) not in transfer_cases() + metadata_cases():
        raise ValueError('Unknown transfer case')
    return '-'.join(case)


def publish(root, filename, data):
    temporary = root / (filename + '.tmp')
    temporary.write_text(json.dumps(data, sort_keys=True) + '\n')
    temporary.replace(root / (filename + '.json'))


def read(root, filename):
    with (root / (filename + '.json')).open('rb') as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError('Endpoint record exceeds limit')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Invalid endpoint record')
    return value


def local_cases(role, field):
    return [case for case in transfer_cases() if case[field] == role]


def serve(root, role, binary, rrsync, address='127.0.0.1', revision=None):
    storage = ExitStack()
    try:
        identity = endpoint_identity(binary, role)
        if revision:
            repository = Path(__file__).resolve().parents[2]
            try:
                actual = subprocess.check_output(['git', '-c', 'safe.directory=' + str(repository), '-C', str(repository),
                                                   'rev-parse', 'HEAD'], stderr=subprocess.PIPE, text=True, timeout=10).strip()
            except (OSError, subprocess.SubprocessError):
                raise ValueError('Native endpoint revision check failed') from None
            if actual != revision:
                raise ValueError('Native endpoint has a different candidate revision')
            identity['source_sha'] = actual
        data = storage.enter_context(metadata_filesystem(root, role))
        (data / 'source').mkdir(parents=True)
        (data / 'destination').mkdir()
        for case in local_cases(role, 0):
            seed(data / 'source' / name(case))
        for case in local_cases(role, 1):
            (data / 'destination' / name(case)).mkdir()
        seed(root / 'reference')
        prepare_metadata(data, role, binary, identity)
        env = {key: os.environ[key] for key in ('PATH', 'RUNNER_TRACKING_ID') if key in os.environ}
        env.update(HOME=str(root), LC_ALL='C')
        conf = root / 'rsyncd.conf'
        conf.write_text(f'use chroot = no\nuid = {os.geteuid()}\ngid = {os.getegid()}\n'
                        f'log file = {root / "rsyncd.log"}\n'
                        f'[data]\npath = {data}\nread only = no\nmunge symlinks = no\n'
                        f'[backup]\npath = {data}\nread only = no\nmunge symlinks = no\nfake super = yes\n')
        daemon = root / 'daemon'
        daemon.mkdir()
        with ExitStack() as resources:
            # Cases have disjoint trees, so the per-directory convenience lock is unnecessary.
            overlay = address != '127.0.0.1'
            ssh = resources.enter_context(SSHServer(root / 'ssh', data, binary, rrsync, address=address,
                                                   port=SSH_PORT if overlay else 0, parallel=True))
            daemon = resources.enter_context(ProcessServer([str(binary), '--daemon', '--no-detach', '--config=' + str(conf)],
                                                          env, daemon, address, DAEMON_PORT if overlay else 0))
            if overlay:
                resources.enter_context(ProcessServer([ssh.sshd, '-i', '-e', '-f', str(ssh.config)],
                                                       ssh.env, ssh.directory, address, 18732))
            keys = {key: public_key(' '.join((root / 'ssh' / (key + '.pub')).read_text().split()[:2]))
                    for key in ('client', 'host')}
            publish(root, 'local', dict(identity=identity, ssh_user=ssh.user, host_key=keys['host'],
                                       client_key=keys['client'], ssh_port=ssh.port, daemon_port=daemon.port))
            deadline = time.monotonic() + 3000
            while not (root / 'stop').exists():
                if time.monotonic() >= deadline:
                    raise ValueError('Endpoint service deadline expired')
                time.sleep(0.1)
    except BaseException as error:
        publish(root, 'error', dict(error=str(error) if isinstance(error, ValueError) else type(error).__name__))
        raise
    finally:
        try:
            storage.close()
        except BaseException:
            if not (root / 'error.json').exists():
                publish(root, 'error', dict(error='Metadata filesystem cleanup failed'))
            raise
        finally:
            for filename in ('client', 'host'):
                path = root / 'ssh' / filename
                if path.exists():
                    path.unlink()
    (root / 'stopped').touch()


def prepare(role, binary, rrsync, address='127.0.0.1', revision=None):
    root = Path(tempfile.mkdtemp(prefix='rsync-interop-', dir=pwd.getpwuid(os.geteuid()).pw_dir))
    env = {key: os.environ[key] for key in ('PATH', 'RUNNER_TRACKING_ID') if key in os.environ}
    with (root / 'agent.log').open('wb') as log:
        subprocess.Popen([sys.executable, '-B', '-S', str(Path(__file__).resolve()), 'serve', role, '--root', str(root),
                          '--binary', str(binary), '--rrsync', str(rrsync), '--address', address,
                          *(['--source-sha', revision] if revision else [])], env=env, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 90
    while not (root / 'local.json').exists():
        if (root / 'error.json').exists():
            raise ValueError(read(root, 'error')['error'])
        if time.monotonic() >= deadline:
            (root / 'stop').touch()
            raise ValueError('Endpoint services did not start')
        time.sleep(0.1)
    return dict(read(root, 'local'), root=str(root), python=sys.executable, script=str(Path(__file__).resolve()))


def configure(root, role, nodes):
    if not isinstance(nodes, dict) or set(nodes) != set(PLATFORMS):
        raise ValueError('Incomplete endpoint set')
    hosts, clients, addresses, host_keys = [], [], set(), set()
    for peer, record in nodes.items():
        if not isinstance(record, dict) or not isinstance(record.get('identity'), dict):
            raise ValueError('Invalid native endpoint record')
        native = record['identity']
        system = native.get('system', '')
        if (native.get('platform') != peer or not isinstance(system, str)
                or not (system.startswith('CYGWIN_') if peer == 'cygwin' else system == PLATFORMS[peer])
                or not isinstance(native.get('binary_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', native['binary_sha256'])
                or not isinstance(record.get('ssh_user'), str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\$?', record['ssh_user'])):
            raise ValueError('Invalid native endpoint identity')
        address = record['address']
        if not isinstance(address, str) or address in addresses or ipaddress.ip_address(address) not in ipaddress.ip_network('100.64.0.0/10'):
            raise ValueError('Invalid or repeated overlay address')
        addresses.add(address)
        host_key = public_key(record['host_key'])
        hosts.append(f'[{address}]:{SSH_PORT} ' + host_key)
        host_keys.add(host_key)
        clients.append(public_key(record['client_key']))
    own = read(root, 'local')
    if (nodes[role]['identity'] != own['identity'] or nodes[role]['host_key'] != own['host_key']
            or nodes[role]['client_key'] != own['client_key'] or len(set(clients)) != len(PLATFORMS)
            or len(host_keys) != len(PLATFORMS) or (root / 'peers.json').exists()):
        raise ValueError('Topology replaced an endpoint identity or reused a key')
    (root / 'ssh' / 'known_hosts').write_text('\n'.join(hosts) + '\n')
    keys = root / 'ssh' / 'authorized_keys.tmp'
    keys.write_text('\n'.join(clients) + '\n')
    keys.chmod(0o600)
    keys.replace(root / 'ssh' / 'authorized_keys')
    publish(root, 'peers', nodes)


def copy(root, binary, peer, transport, push, local, remote_path, options, fake_remote=False):
    if transport == 'ssh':
        remote = f'{peer["ssh_user"]}@{peer["address"]}:{remote_path}'
        protocol = ['--old-args', '--rsync-path=rsync', '-e', quote_command(client_command(root / 'ssh', SSH_PORT))]
        if fake_remote:
            protocol.append('-M--fake-super')
    else:
        module = 'backup' if fake_remote else 'data'
        remote = f'rsync://{peer["address"]}:{DAEMON_PORT}/{module}/{remote_path}'
        protocol = []
    operands = [str(local) + '/', remote] if push else [remote, str(local) + '/']
    env = {key: os.environ[key] for key in ('PATH', 'RUNNER_TRACKING_ID') if key in os.environ}
    return command([str(binary), *options, *protocol, *operands], env, timeout=60)


def prepare_metadata(data, role, binary, identity):
    base = data / 'metadata'
    for part in ('source', 'destination', 'restored'):
        (base / part).mkdir(parents=True)
    identity['metadata'] = qualify(binary, base / 'source/backup', role)
    for kind in ('acl', 'xattr'):
        seed_metadata(base / 'source' / kind, role, kind)
    if FEATURES[role][1] and role != 'cygwin':
        seed_metadata(base / 'source/empty-xattr', role, 'empty-xattr')
    for case in metadata_cases():
        if case[1] == role:
            (base / 'destination' / name(case)).mkdir()


def metadata_transfers(root, role, binary, nodes, phase):
    commands, restored = [], []
    for case in metadata_cases():
        source, destination, client, transport, kind = case
        if (source if client == 'push' else destination) != role:
            continue
        if phase == 2 and (kind == 'backup' or not supported(case)):
            continue
        source_path = 'metadata/source/' + kind + '/'
        destination_path = 'metadata/destination/' + name(case) + '/'
        peer = nodes[destination if client == 'push' else source]
        local = root / 'data' / (source_path if client == 'push' else destination_path)
        options = ['-rltp', '--numeric-ids', '-A' if kind in ('acl', 'backup') else '-X']
        if kind == 'backup':
            if FEATURES[source][1]:
                options.append('-X')
            if client == 'pull':
                options.append('--fake-super')
        result = copy(root, binary, peer, transport, client == 'push', local,
                      destination_path if client == 'push' else source_path, options,
                      fake_remote=kind == 'backup' and client == 'push')
        if supported(case):
            if result.returncode:
                raise ValueError(name(case) + ': metadata transfer failed: ' + result.stderr.decode(errors='replace')[-2048:])
        elif kind == 'empty-xattr' and destination == 'cygwin':
            if result.returncode != 23 or b'Invalid argument' not in result.stderr:
                raise ValueError(name(case) + ': empty NTFS xattr was not explicitly refused')
        elif (result.returncode == 0
              or (b'ACLs are not supported' if kind == 'acl' else b'extended attributes are not supported') not in result.stderr):
            raise ValueError(name(case) + ': unsupported metadata was not explicitly refused')
        commands.append(list(case))
    if phase == 2:
        for case in metadata_cases():
            if case[0] != role or case[-1] != 'backup':
                continue
            source, destination, client, transport, kind = case
            target = root / 'data' / 'metadata/restored' / name(case)
            options = ['-rltpA', '--numeric-ids', *(['-X'] if FEATURES[source][1] else [])]
            result = copy(root, binary, nodes[destination], transport, False, target,
                          'metadata/destination/' + name(case) + '/', options, fake_remote=True)
            expected = root / 'data' / 'metadata/source/backup'
            if result.returncode or metadata_snapshot(expected, role, kind) != metadata_snapshot(target, role, kind):
                raise ValueError(name(case) + ': cross-platform backup did not restore its original ACLs or xattrs')
            restored.append(list(case))
    return dict(metadata_commands=commands, metadata_restored=restored)


def transfers(root, role, binary, phase):
    nodes = read(root, 'peers')
    commands = []
    for case in transfer_cases():
        source, destination, client, transport = case
        if (source if client == 'push' else destination) != role:
            continue
        source_path, destination_path = 'source/' + name(case) + '/', 'destination/' + name(case) + '/'
        peer = nodes[destination if client == 'push' else source]
        result = copy(root, binary, peer, transport, client == 'push',
                      root / 'data' / (source_path if client == 'push' else destination_path),
                      destination_path if client == 'push' else source_path,
                      ['-rltpH', *(['--checksum', '--delete'] if phase == 2 else [])])
        if result.returncode:
            raise ValueError(f'{name(case)}: rsync exited {result.returncode}: '
                             + result.stderr.decode(errors='replace')[-2048:])
        commands.append(list(case))
    result = dict(commands=commands, **metadata_transfers(root, role, binary, nodes, phase))
    publish(root, 'transferred-' + str(phase), result)
    return result


def verify_metadata(root, role, phase):
    base = root / 'data' / 'metadata'
    expected = {kind: metadata_snapshot(base / 'source' / kind, role, kind, phase)
                for kind, available in (('acl', FEATURES[role][0]), ('xattr', FEATURES[role][1]),
                                        ('empty-xattr', FEATURES[role][1] and role != 'cygwin')) if available}
    verified = []
    for case in metadata_cases():
        if case[1] != role:
            continue
        target, kind = base / 'destination' / name(case), case[-1]
        if not supported(case):
            if kind == 'empty-xattr' and role == 'cygwin':
                if ((target / 'directory/file').read_bytes() != b'metadata transfer\n'
                        or 'rsync-ci-empty' in xattrs.dump_values(target / 'directory', target / 'directory/file')):
                    raise ValueError(name(case) + ': empty NTFS xattr refusal had an unexpected result')
            elif any(target.iterdir()):
                raise ValueError(name(case) + ': refused metadata transfer wrote destination files')
        elif kind == 'backup':
            for path in (target / 'directory', target / 'directory/file'):
                if not xattrs.get_value('rsync.%aacl', path):
                    raise ValueError(name(case) + ': backup has no saved ACL')
            if (target / 'directory/file').read_bytes() != b'metadata transfer\n':
                raise ValueError(name(case) + ': backup file contents differ')
        elif expected[kind] != metadata_snapshot(target, role, kind, phase):
            raise ValueError(name(case) + ': destination ACLs or xattrs differ')
        verified.append(list(case))
    if phase == 1:
        for kind in ('acl', 'xattr'):
            seed_metadata(base / 'source' / kind, role, kind, 2)
    return verified


def verify(root, role, phase):
    expected = snapshot(root / 'reference')
    verified = []
    for case in local_cases(role, 1):
        if snapshot(root / 'data' / 'destination' / name(case)) != expected:
            raise ValueError(name(case) + ': transferred data or metadata differ')
        verified.append(list(case))
    if phase == 1:
        for case in local_cases(role, 0):
            update(root / 'data' / 'source' / name(case))
        update(root / 'reference')
    result = dict(verified=verified, metadata_verified=verify_metadata(root, role, phase))
    publish(root, 'verified-' + str(phase), result)
    return result


def stop(root):
    (root / 'stop').touch()
    deadline = time.monotonic() + 15
    while not (root / 'stopped').exists():
        if (root / 'error.json').exists():
            raise ValueError(read(root, 'error')['error'])
        if time.monotonic() >= deadline:
            raise ValueError('Endpoint services did not stop')
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('prepare', 'serve', 'configure', 'transfer1', 'next', 'transfer2', 'verify', 'stop'))
    parser.add_argument('platform', choices=PLATFORMS)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--rrsync', type=Path)
    parser.add_argument('--address', default='127.0.0.1')
    parser.add_argument('--source-sha')
    args = parser.parse_args()
    if (args.phase in ('prepare', 'serve') and (args.binary is None or args.rrsync is None)
            or args.phase != 'prepare' and args.root is None):
        parser.error('The phase is missing its endpoint paths')
    if args.source_sha is not None and not re.fullmatch(r'[0-9a-f]{40}', args.source_sha):
        parser.error('Invalid source revision')
    def interrupted(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise ValueError('Endpoint interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        result = {}
        if args.phase in ('prepare', 'serve'):
            binary, rrsync = args.binary.resolve(strict=True), args.rrsync.resolve(strict=True)
            if args.phase == 'prepare':
                result = prepare(args.platform, binary, rrsync, args.address, args.source_sha)
            else:
                serve(args.root, args.platform, binary, rrsync, args.address, args.source_sha)
        elif args.phase == 'configure':
            data = sys.stdin.buffer.read(65537)
            if len(data) > 65536:
                raise ValueError('Peer manifest exceeds limit')
            configure(args.root, args.platform, json.loads(data))
        elif args.phase in ('transfer1', 'transfer2'):
            result = transfers(args.root, args.platform, args.binary.resolve(strict=True), int(args.phase[-1]))
        elif args.phase in ('next', 'verify'):
            result = verify(args.root, args.platform, 1 if args.phase == 'next' else 2)
        else:
            stop(args.root)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Endpoint failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
