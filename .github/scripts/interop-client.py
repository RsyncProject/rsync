import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

from interop_network import NetworkError, NoRedirect

VERSION = '1.104.4'
LINUX_SHA = '624ea093d47d62cf56f17b8332d74b8decda4a1faa610ebc60a1de334b2b3dc9'
WINDOWS_SHA = '1d12cd8b1d6dbb447da8579b1a5268ae2fc050967bebfaa02e32d2bb645ef6ee'
SOURCE = '27047635f237d1f3e0b08c0f487674757f30d2f7'


def run(argv, timeout=60, check=True, **options):
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, **options)
    except FileNotFoundError:
        raise NetworkError('Required command not found: ' + Path(argv[0]).name) from None
    if check and result.returncode:
        raise NetworkError(Path(argv[0]).name + ' failed with exit code ' + str(result.returncode))
    return result


def download(url, path, wanted):
    digest, size = hashlib.sha256(), 0
    with urllib.request.build_opener(NoRedirect()).open(url, timeout=30) as stream, path.open('xb') as output:
        while True:
            data = stream.read(65536)
            if not data:
                break
            size += len(data)
            if size > 150 * 1024 * 1024:
                raise NetworkError('Tailscale package exceeds limit')
            digest.update(data)
            output.write(data)
    if digest.hexdigest() != wanted:
        raise NetworkError('Tailscale package checksum differs')


def install(root):
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise NetworkError('Client installation is restricted to disposable hosted runners')
    binary = root / 'bin'
    binary.mkdir()
    if sys.platform.startswith('cygwin'):
        package = root / 'tailscale.msi'
        download(f'https://pkgs.tailscale.com/stable/tailscale-setup-{VERSION}-amd64.msi', package, WINDOWS_SHA)
        native = run(['cygpath', '-w', str(package)]).stdout.decode().strip()
        run(['msiexec.exe', '/quiet', '/norestart', '/i', native], timeout=180)
        return dict(command=['/cygdrive/c/Program Files/Tailscale/tailscale.exe'], windows=True)
    if sys.platform == 'darwin':
        env = dict(os.environ, GOBIN=str(binary), GOENV='off', GOWORK='off', GOFLAGS='',
                   GOPROXY='https://proxy.golang.org', GOSUMDB='sum.golang.org', GONOSUMDB='', GOPRIVATE='')
        run(['go', 'install', 'tailscale.com/cmd/tailscale@' + SOURCE,
             'tailscale.com/cmd/tailscaled@' + SOURCE], env=env, timeout=900)
    else:
        package = root / 'tailscale.tgz'
        download(f'https://pkgs.tailscale.com/stable/tailscale_{VERSION}_amd64.tgz', package, LINUX_SHA)
        with tarfile.open(str(package), 'r:gz') as archive:
            for name in ('tailscale', 'tailscaled'):
                members = [member for member in archive.getmembers() if member.name == f'tailscale_{VERSION}_amd64/{name}']
                if len(members) != 1 or not members[0].isreg() or members[0].size > 100 * 1024 * 1024:
                    raise NetworkError('Unexpected client package member')
                with archive.extractfile(members[0]) as source, (binary / name).open('xb') as output:
                    shutil.copyfileobj(source, output)
                (binary / name).chmod(0o755)
    return dict(command=['sudo', str(binary / 'tailscale'), '--socket=' + str(root / 'tailscaled.sock')], windows=False)


def daemon(root):
    env = {key: os.environ[key] for key in ('PATH', 'RUNNER_TRACKING_ID') if key in os.environ}
    with (root / 'tailscaled.log').open('wb') as log:
        with subprocess.Popen([str(root / 'bin' / 'tailscaled'), '--state=mem:', '--socket=' + str(root / 'tailscaled.sock')],
                              env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log) as child:
            try:
                deadline = time.monotonic() + 3000
                while not (root / 'daemon-stop').exists() and time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise NetworkError('Tailscale daemon exited')
                    time.sleep(0.1)
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                (root / 'daemon-stopped').touch()


def connect(root, key, hostname):
    client = json.loads((root / 'client.json').read_text())
    if not client['windows']:
        env = {name: os.environ[name] for name in ('PATH', 'RUNNER_TRACKING_ID') if name in os.environ}
        subprocess.Popen(['sudo', '--preserve-env=RUNNER_TRACKING_ID', sys.executable, '-B', '-S',
                          str(Path(__file__).resolve()), 'daemon', '--root', str(root)],
                         env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        (root / 'daemon-started').touch()
    deadline = time.monotonic() + 30
    while True:
        result = run([*client['command'], 'status', '--json'], check=False)
        if result.stdout.startswith(b'{'):
            break
        if time.monotonic() >= deadline:
            raise NetworkError('Tailscale daemon did not start')
        time.sleep(0.2)
    path = str(key)
    if client['windows']:
        path = run(['cygpath', '-w', path]).stdout.decode().strip()
    run([*client['command'], 'up', '--auth-key=file:' + path, '--hostname=' + hostname,
         '--accept-routes=false', '--accept-dns=false', '--timeout=60s'], timeout=75)
    prefs = json.loads(run([*client['command'], 'debug', 'prefs']).stdout)
    if any(prefs.get(key) is not False for key in ('RouteAll', 'CorpDNS')):
        raise NetworkError('Route or DNS acceptance is still enabled')
    return json.loads(run([*client['command'], 'status', '--json']).stdout)['Self']


def stop(root):
    if not (root / 'client.json').exists():
        return
    client = json.loads((root / 'client.json').read_text())
    if not client['windows'] and not (root / 'daemon-started').exists():
        return
    try:
        run([*client['command'], 'logout'])
    finally:
        if client['windows']:
            run(['sc.exe', 'stop', 'Tailscale'], check=False)
        else:
            (root / 'daemon-stop').touch()
            deadline = time.monotonic() + 15
            while not (root / 'daemon-stopped').exists():
                if time.monotonic() >= deadline:
                    raise NetworkError('Tailscale daemon did not stop')
                time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('daemon', 'stop'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    try:
        daemon(args.root) if args.phase == 'daemon' else stop(args.root)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Tailscale client failed: ' + type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
