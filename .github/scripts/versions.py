import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys


def main():
    argparse.ArgumentParser(description='Run historical-peer checks').parse_args()
    output = Path('test-results/version-mix')
    output.mkdir(parents=True)
    peers = sorted(Path('old_versions').glob('rsync_*'))
    if not peers:
        raise ValueError('No historical peers found')
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
        for peer in peers:
            peer.chmod(peer.stat().st_mode | 0o111)
            profile = 'peer-' + peer.name.removeprefix('rsync_')
            for transport in ('pipe', 'tcp'):
                print(f'::group::{peer.name} ({transport})', flush=True)
                try:
                    command = [sys.executable, '-S', 'testsuite/tools/check.py', '.', './rsync',
                               f'linux,pre-3.4.3,{profile}', str(output / f'{peer.name}-{transport}'),
                               f'--rsync-bin2={peer.resolve()}', '-j8']
                    if transport == 'tcp':
                        command += ['--use-tcp', '--daemon-tests-only']
                    active = subprocess.Popen(command, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                    if active.wait():
                        print(f'::error::Version mix failed: {peer.name} ({transport})', file=sys.stderr)
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
