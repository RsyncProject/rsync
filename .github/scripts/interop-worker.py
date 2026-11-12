import argparse
import errno
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import socket
import subprocess
import sys
import time

from interop_network import NetworkError, SLOTS, artifacts, context, worker_authority
from interop_policy import address


def write(root, name, data):
    (root / (name + '.json')).write_text(json.dumps(dict(context(), **data)) + '\n')


def connect(ip, port, nonce=None):
    try:
        connection = socket.create_connection((ip, port), timeout=2)
    except OSError as error:
        if error.errno not in (errno.ETIMEDOUT, errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH,
                               errno.EACCES, errno.EPERM) and not isinstance(error, socket.timeout):
            raise
        return False
    with connection:
        if nonce is not None:
            reply = b''
            while len(reply) < len(nonce):
                part = connection.recv(len(nonce) - len(reply))
                if not part:
                    break
                reply += part
            if reply != nonce.encode():
                raise NetworkError('Canary identity mismatch')
    return True


def health(ready):
    for port in (18731, 18732):
        if not connect(ready['address'], port, ready['nonce']):
            raise NetworkError('Local canary is not listening')


def denied(ip, port):
    if connect(ip, port):
        raise NetworkError('Forbidden TCP connection succeeded')


def wait_until(value):
    if type(value) not in (int, float) or not time.time() - 5 <= value <= time.time() + 180:
        raise NetworkError('Invalid probe barrier')
    time.sleep(max(0, value - time.time()))


def serve(root):
    ready = json.loads((root / 'ready.json').read_text())
    with selectors.DefaultSelector() as selector:
        listeners = []
        try:
            for port in (18731, 18732):
                listener = socket.socket()
                listeners.append(listener)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((ready['address'], port))
                listener.listen(32)
                listener.setblocking(False)
                selector.register(listener, selectors.EVENT_READ)
            (root / 'listening').touch()
            deadline = time.monotonic() + 1500
            while time.monotonic() < deadline:
                for key, _ in selector.select(min(2, max(0, deadline - time.monotonic()))):
                    with key.fileobj.accept()[0] as connection:
                        connection.settimeout(2)
                        try:
                            connection.sendall(ready['nonce'].encode())
                        except (BrokenPipeError, ConnectionResetError, socket.timeout):
                            pass
        finally:
            for listener in listeners:
                listener.close()


def configure_network():
    subprocess.run(['sudo', 'tailscale', 'set', '--accept-routes=false', '--accept-dns=false'],
                   capture_output=True, timeout=15, check=True)
    result = subprocess.run(['sudo', 'tailscale', 'debug', 'prefs'], capture_output=True, timeout=15, check=True)
    prefs = json.loads(result.stdout)
    if any(prefs.get(key) is not False for key in ('RouteAll', 'CorpDNS')):
        raise NetworkError('Route or DNS acceptance is still enabled')
    return {key: prefs[key] for key in ('RouteAll', 'CorpDNS')}


def prepare(root, slot):
    prefs = configure_network()
    oidc = worker_authority()
    enrolment = json.loads((root / 'enrolment.json').read_text())
    if any(enrolment.get(key) != value for key, value in dict(context(), slot=slot).items()):
        raise NetworkError('Worker enrolment record is stale')
    result = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, timeout=15, check=True)
    node = json.loads(result.stdout)['Self']
    ips = [ip for ip in node['TailscaleIPs'] if ':' not in ip]
    if len(ips) != 1 or not isinstance(node['ID'], str) or not node['ID']:
        raise NetworkError('Missing local Tailscale identity')
    ready = dict(slot=slot, node_id=node['ID'], address=address(ips[0]), nonce=secrets.token_hex(24),
                 oidc_available=oidc, key_id=enrolment['key_id'])
    ready['preferences'] = prefs
    write(root, 'ready', ready)
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'RUNNER_TRACKING_ID') if key in os.environ}
    with (root / 'server.log').open('wb') as log:
        child = subprocess.Popen([sys.executable, '-B', '-S', str(Path(__file__).resolve()), 'serve', slot,
                                  '--root', str(root)], env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    start = Path('/proc') / str(child.pid) / 'stat'
    (root / 'process.json').write_text(json.dumps(dict(pid=child.pid, start=start.read_text().split()[21])))
    deadline = time.monotonic() + 10
    while not (root / 'listening').exists():
        if child.poll() is not None or time.monotonic() >= deadline:
            raise NetworkError('Canary did not start')
        time.sleep(0.1)
    health(ready)


def probe(root, slot, phase):
    ready = json.loads((root / 'ready.json').read_text())
    topology = artifacts('topology')[None]
    nodes = topology['nodes']
    if set(nodes) != set(SLOTS) or nodes[slot] != ready:
        raise NetworkError('Topology does not match this worker')
    for name, node in nodes.items():
        if node.get('slot') != name or address(node['address']) != node['address']:
            raise NetworkError('Invalid peer identity')
    peer = nodes[slot[0] + ('2' if slot[1] == '1' else '1')]
    health(ready)
    if phase == 'quarantine':
        wait_until(topology['start_at'])
        for name, node in nodes.items():
            if name != slot:
                denied(node['address'], 18731)
    elif phase == 'exercise':
        release = artifacts('admission')[None]
        if release['nodes'] != nodes:
            raise NetworkError('Admission changed endpoint identities')
        wait_until(release['start_at'])
        deadline = time.monotonic() + 45
        while not connect(peer['address'], 18731, peer['nonce']):
            if time.monotonic() >= deadline:
                raise NetworkError('Permitted peer connection did not succeed')
            time.sleep(1)
        for name, node in nodes.items():
            if name[0] != slot[0]:
                denied(node['address'], 18731)
        denied(peer['address'], 18732)
        if not connect(peer['address'], 18731, peer['nonce']):
            raise NetworkError('Permitted connection failed after denial probes')
    else:
        release = artifacts('revocation')[None]
        wait_until(release['start_at'])
        if release['end_at'] != release['start_at'] + 90:
            raise NetworkError('Invalid revocation window')
        deadline = time.monotonic() + 45
        while connect(peer['address'], 18731):
            if time.monotonic() >= deadline:
                raise NetworkError('Revoked connection remained permitted')
            time.sleep(1)
        for _ in range(3):
            denied(peer['address'], 18731)
        remaining = release['end_at'] - time.time()
        if remaining < 0:
            raise NetworkError('Revocation probe exceeded the live-listener window')
        time.sleep(remaining)
    health(ready)
    write(root, phase, dict(slot=slot, node_id=ready['node_id'], passed=True, finished_at=time.time()))


def process_state(pid):
    try:
        fields = (Path('/proc') / str(pid) / 'stat').read_text().split()
    except FileNotFoundError:
        return None
    return fields[2], fields[21]


def stop(root):
    path = root / 'process.json'
    if not path.exists():
        return
    state = json.loads(path.read_text())
    current = process_state(state['pid'])
    if current is None:
        return
    if current[1] != state['start']:
        raise NetworkError('Canary process identity changed')
    try:
        if os.getpgid(state['pid']) != state['pid']:
            raise NetworkError('Canary process group changed')
        os.killpg(state['pid'], signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = process_state(state['pid'])
        if current is None or current[0] == 'Z':
            return
        if current[1] != state['start']:
            raise NetworkError('Canary process identity changed during shutdown')
        time.sleep(0.1)
    current = process_state(state['pid'])
    if current is None or current[0] == 'Z':
        return
    if current[1] != state['start']:
        raise NetworkError('Canary process identity changed before forced shutdown')
    try:
        os.killpg(state['pid'], signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = process_state(state['pid'])
        if current is None or current[0] == 'Z':
            return
        time.sleep(0.1)
    raise NetworkError('Canary process did not stop')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('prepare', 'serve', 'quarantine', 'exercise', 'revoked', 'stop'))
    parser.add_argument('slot', choices=SLOTS)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        if args.phase == 'serve':
            serve(root)
        elif args.phase == 'stop':
            stop(root)
        else:
            context()
            if args.phase == 'prepare':
                prepare(root, args.slot)
            else:
                probe(root, args.slot, args.phase)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, NetworkError) else type(error).__name__
        print('Interoperability worker failed during ' + args.phase + ': ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
