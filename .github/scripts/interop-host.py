import argparse
import ipaddress
import json
import os
from pathlib import Path
import runpy
import shlex
import socket
import subprocess
import sys
import time

from interop_ci import ROLES, PeerUnavailable, identity, published, records, validate, write
from interop_enrol import decrypt, forget, generate
from interop_network import NetworkError, require_no_oidc

client = runpy.run_path(str(Path(__file__).with_name('interop-client.py')))


def path(value):
    if sys.platform.startswith('cygwin') and len(value) > 2 and value[1] == ':':
        value = subprocess.check_output(['cygpath', '-u', value], text=True, timeout=10).strip()
    return Path(value)


def call(root, role, phase, data=None):
    guest = os.environ.get('INTEROP_GUEST') == 'true'
    workspace = path(os.environ['GITHUB_WORKSPACE'])
    args = ['python3', '-B', '-S', str(workspace / '.github/scripts/interop-endpoint.py'), phase, role]
    if phase == 'prepare':
        args += ['--binary', str(workspace / ('rsync.exe' if role == 'cygwin' else 'rsync')),
                 '--rrsync', str(workspace / 'support/rrsync'), '--source-sha', identity()['source_sha']]
        if not guest:
            args += ['--address', json.loads((root / 'node.json').read_text())['address']]
    else:
        endpoint = json.loads((root / 'endpoint.json').read_text())
        args += ['--root', endpoint['root']]
        if phase in ('transfer1', 'transfer2'):
            args += ['--binary', str(workspace / ('rsync.exe' if role == 'cygwin' else 'rsync'))]
    if guest:
        remote = ['env', 'PATH=/usr/local/bin:/usr/local/sbin:/usr/pkg/bin:/usr/pkg/sbin:/usr/gnu/bin:/usr/bin:/bin:/usr/sbin:/sbin', *args]
        args = ['ssh', '-oBatchMode=yes', '-oConnectTimeout=10', role, ' '.join(map(shlex.quote, remote))]
    else:
        args[0] = sys.executable
        if not sys.platform.startswith('cygwin'):
            args = ['sudo', '--preserve-env=RUNNER_TRACKING_ID', *args]
    result = subprocess.run(args, input=None if data is None else json.dumps(data).encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=1600 if phase.startswith('transfer') else (120 if phase in ('prepare', 'next', 'verify') else 45))
    if result.returncode:
        raise NetworkError(f'{role} {phase} failed: ' + result.stderr.decode(errors='replace')[-2048:])
    if len(result.stdout) > 65536:
        raise NetworkError('Native endpoint output exceeds limit')
    return json.loads(result.stdout)


def forward(root, role):
    node = json.loads((root / 'node.json').read_text())
    endpoint = json.loads((root / 'endpoint.json').read_text())
    argv = ['ssh', '-N', '-oBatchMode=yes', '-oExitOnForwardFailure=yes', '-oConnectTimeout=10']
    for port, target in ((18731, endpoint['ssh_port']), (18732, endpoint['ssh_port']), (18733, endpoint['daemon_port'])):
        argv += ['-L', f'{node["address"]}:{port}:127.0.0.1:{target}']
    argv.append(role)
    with (root / 'forward.log').open('wb') as log, subprocess.Popen(argv, stdout=log, stderr=log) as process:
        try:
            deadline = time.monotonic() + 3000
            while not (root / 'forward-stop').exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise NetworkError('Guest fixture forwarding exited')
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            (root / 'forward-stopped').touch()


def connect(address, port):
    try:
        with socket.create_connection((address, port), timeout=2):
            return True
    except (socket.timeout, ConnectionRefusedError, ConnectionResetError, PermissionError):
        return False
    except OSError as error:
        import errno
        if error.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT):
            return False
        raise


def healthy(node):
    if not all(connect(node['address'], port) for port in (18731, 18732, 18733)):
        raise NetworkError('Local fixture services are not listening')


def network(root, role, phase):
    own = json.loads((root / 'ready.json').read_text())
    topology = published('topology')['nodes']
    if set(topology) != set(ROLES) or topology[role] != own:
        raise NetworkError('Network topology changed the local endpoint')
    healthy(own)
    if phase == 'quarantine':
        if any(connect(peer['address'], 18731) for name, peer in topology.items() if name != role):
            raise NetworkError('A quarantined peer is reachable')
    elif phase == 'admit':
        admission = published('admission')
        if admission['nodes'] != topology:
            raise NetworkError('Admission replaced the endpoint set')
        deadline = time.monotonic() + 60
        for name, peer in topology.items():
            if name == role:
                continue
            while not all(connect(peer['address'], port) for port in (18731, 18733)):
                if time.monotonic() >= deadline:
                    raise NetworkError('An admitted endpoint is unreachable')
                time.sleep(1)
            if connect(peer['address'], 18732):
                raise NetworkError('A forbidden fixture port is reachable')
        call(root, role, 'configure', topology)
    else:
        if published('revocation').get('revoked') is not True:
            raise NetworkError('Network revocation was not recorded')
        deadline = time.monotonic() + 60
        for name, peer in topology.items():
            if name == role:
                continue
            while any(connect(peer['address'], port) for port in (18731, 18733)):
                if time.monotonic() >= deadline:
                    raise NetworkError('A revoked endpoint remains reachable')
                time.sleep(1)
    healthy(own)
    if phase != 'admit':
        write(root, phase, role=role, node_id=own['node_id'], passed=True)
    else:
        write(root, 'admitted', role=role, node_id=own['node_id'], passed=True)


def stop(root, role):
    errors = []
    if (root / 'endpoint.json').exists():
        try:
            call(root, role, 'stop')
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append(type(error).__name__)
    if (root / 'forward.started').exists():
        (root / 'forward-stop').touch()
        deadline = time.monotonic() + 15
        while not (root / 'forward-stopped').exists():
            if time.monotonic() >= deadline:
                errors.append('Guest forwarding did not stop')
                break
            time.sleep(0.1)
    try:
        client['stop'](root)
    finally:
        forget(root)
    if errors:
        raise NetworkError('; '.join(errors))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('install', 'recipient', 'connect', 'prepare', 'forward', 'quarantine', 'admit',
                                        'transfer1', 'next', 'transfer2', 'verify', 'revoked', 'stop', 'result'))
    parser.add_argument('platform', choices=(*ROLES, 'all'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = path(str(args.root)).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        require_no_oidc()
        role, phase = args.platform, args.phase
        if phase == 'stop':
            stop(root, role)
            return 0
        if phase == 'forward':
            forward(root, role)
            return 0
        if phase == 'install':
            (root / 'client.json').write_text(json.dumps(client['install'](root)))
            return 0
        state = identity()
        if phase == 'recipient':
            generate(root, role, state)
        elif phase == 'connect':
            enrolment = published('enrol')['recipients'][role]
            decrypt(root, role, enrolment, state)
            try:
                node = client['connect'](root, root / 'enrol-key', f'ci-{state["run_id"]}-{state["run_attempt"]}-{role}')
            finally:
                forget(root)
            addresses = [value for value in node['TailscaleIPs'] if ':' not in value]
            if len(addresses) != 1 or ipaddress.ip_address(addresses[0]) not in ipaddress.ip_network('100.64.0.0/10'):
                raise NetworkError('No unique Tailscale IPv4 address')
            deadline = time.monotonic() + 15
            while True:
                try:
                    with socket.socket() as listener:
                        listener.bind((addresses[0], 0))
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise NetworkError('The allocated overlay address is not available locally') from None
                    time.sleep(0.1)
            (root / 'node.json').write_text(json.dumps(dict(node_id=node['ID'], address=addresses[0], key_id=enrolment['key_id'])))
        elif phase == 'prepare':
            endpoint = call(root, role, 'prepare')
            if endpoint.get('identity', {}).get('platform') != role:
                raise NetworkError('The host substituted for the native endpoint')
            (root / 'endpoint.json').write_text(json.dumps(endpoint))
            if os.environ.get('INTEROP_GUEST') == 'true':
                env = {key: os.environ[key] for key in ('PATH', 'RUNNER_TRACKING_ID') if key in os.environ}
                subprocess.Popen([sys.executable, '-B', '-S', str(Path(__file__).resolve()), 'forward', role,
                                  '--root', str(root)], env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                (root / 'forward.started').touch()
                own = json.loads((root / 'node.json').read_text())
                deadline = time.monotonic() + 15
                while not connect(own['address'], 18731):
                    if time.monotonic() >= deadline:
                        raise NetworkError('Guest fixture forwarding did not start')
                    time.sleep(0.1)
            public = {key: endpoint[key] for key in ('identity', 'ssh_user', 'host_key', 'client_key')}
            write(root, 'ready', **public, **json.loads((root / 'node.json').read_text()), role=role, oidc_available=False)
        elif phase in ('quarantine', 'admit', 'revoked'):
            network(root, role, phase)
        elif phase in ('transfer1', 'transfer2'):
            records('admitted' if phase == 'transfer1' else 'next', timeout=600)
            result = call(root, role, phase)
            write(root, phase, **result)
        elif phase in ('next', 'verify'):
            records('transfer1' if phase == 'next' else 'transfer2', timeout=1800)
            result = call(root, role, phase)
            write(root, phase, **result)
            if phase == 'verify':
                own = json.loads((root / 'ready.json').read_text())
                initial = json.loads((root / 'transfer1.json').read_text())
                initial_check = json.loads((root / 'next.json').read_text())
                updated = json.loads((root / 'transfer2.json').read_text())
                write(root, 'result', identity=own['identity'], node_id=own['node_id'], passed=True,
                      commands=updated['commands'], verified=result['verified'],
                      metadata_commands=initial['metadata_commands'], metadata_updates=updated['metadata_commands'],
                      metadata_initial_verified=initial_check['metadata_verified'],
                      metadata_verified=result['metadata_verified'], metadata_restored=updated['metadata_restored'])
        else:
            jobs = json.loads(os.environ['INTEROP_JOBS'])
            if set(jobs) != set(ROLES):
                raise NetworkError('Incomplete endpoint job set')
            incomplete = [role for role, job in jobs.items()
                          if job.get('result') != 'success' or job.get('outputs', {}).get('blocked') != 'false']
            if incomplete:
                raise NetworkError('Failed or blocked endpoints: ' + ', '.join(incomplete))
            broker = runpy.run_path(str(Path(__file__).with_name('interop-broker.py')))
            broker['verify_cases'](records('result'), state['source_sha'])
            proof = published('result')
            if proof.get('passed') is not True or proof.get('cleaned') is not True or proof.get('roles') != list(ROLES):
                raise NetworkError('Missing verified network cleanup')
            print('Verified directed transfers, metadata restoration and network cleanup')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        if isinstance(error, PeerUnavailable) and args.phase not in ('result', 'stop'):
            write(root, 'blocked', role=args.platform, phase=args.phase, passed=False, reason=str(error))
            with open(os.environ['GITHUB_ENV'], 'a') as stream:
                stream.write('INTEROP_BLOCKED=true\n')
            print('Interoperability blocked: ' + str(error))
            return 0
        detail = str(error) if isinstance(error, NetworkError) else type(error).__name__
        print('Interoperability failed: ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
