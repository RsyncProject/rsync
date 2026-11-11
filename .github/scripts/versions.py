import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'testsuite'))
from harness.profile import load_binaries


def main():
    parser = argparse.ArgumentParser(description='Run historical-peer checks')
    parser.add_argument('--role', choices=('primary', 'secondary'), required=True)
    parser.add_argument('--current', default='rsync', help='Current rsync executable')
    parser.add_argument('--output', default='test-results/version-mix', help='Result directory')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    archive = Path('old_versions')
    records = load_binaries(archive)
    current = Path(args.current).resolve(strict=True)
    status = 0
    active = None

    def cancel(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if active is not None and active.poll() is None:
            active.terminate()
            try:
                active.wait(timeout=10)
            except subprocess.TimeoutExpired:
                active.kill()
                active.wait(timeout=5)
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for name in sorted(records, key=lambda value: tuple(
                int(part) for part in value.removeprefix('rsync_').split('.'))):
            peer = archive / name
            peer.chmod(peer.stat().st_mode | 0o111)
            version = peer.name.removeprefix('rsync_')
            profile = 'peer-' + version
            primary, secondary = ((peer.resolve(), current) if args.role == 'primary'
                                  else (current, peer.resolve()))
            for transport in ('pipe', 'tcp'):
                label = f'{peer.name} ({args.role}, {transport})'
                profiles = ['non-asan', 'nonroot', 'linux', profile]
                if transport == 'pipe':
                    profiles.insert(0, 'pipe')
                print(f'::group::{label}', flush=True)
                try:
                    command = [sys.executable, '-S', 'testsuite/tools/check.py', '.', str(primary),
                               ','.join(profiles),
                               str(output / f'{peer.name}-{args.role}-{transport}'),
                               f'--rsync-bin2={secondary}', f'--archived-role={args.role}', '-j8']
                    if transport == 'tcp':
                        command += ['--use-tcp', '--daemon-tests-only']
                    active = subprocess.Popen(command, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                    if active.wait():
                        print(f'::error::Version mix failed: {label}', file=sys.stderr)
                        status = 1
                finally:
                    active = None
                    print('::endgroup::', flush=True)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return status


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Version mix failed: {error}', file=sys.stderr)
        raise SystemExit(1)
