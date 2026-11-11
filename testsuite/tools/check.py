#!/usr/bin/env python3

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def main():
    args = sys.argv[1:]
    if len(args) < 4 or not args[2]:
        print('Usage: check.py BUILD_DIR RSYNC PROFILES OUTPUT_DIR [TEST_OPTIONS...]', file=sys.stderr)
        return 2
    build, binary, profiles, output, *options = args
    build = Path(build).resolve(strict=True)
    binary = Path(binary).absolute()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        print(f'Not an executable file: {binary}', file=sys.stderr)
        return 2
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    output = output.resolve()
    (output / 'scratch').mkdir()
    source = Path(__file__).resolve().parents[2]
    runner = source / 'testsuite' / 'runtests.py'
    command = [sys.executable, '-S', str(runner)]

    def fixed_options():
        selected = []
        for index, option in enumerate(options):
            for name in ('--rsync-bin2', '--archived-role'):
                if option == name and index + 1 < len(options):
                    selected.extend((name, options[index + 1]))
                elif option.startswith(name + '='):
                    selected.append(option)
        return selected
    env = os.environ.copy()
    env.pop('whichtests', None)
    env['scratchbase'] = str(output / 'scratch')
    proc = None
    interrupted = None
    deadline = None

    def cancel(signum, frame):
        nonlocal interrupted, deadline
        if interrupted is None:
            interrupted = signum
            deadline = time.monotonic() + 10
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}

    def execute(argv, stdout=None):
        nonlocal proc
        if interrupted is not None:
            return 128 + interrupted
        proc = subprocess.Popen(argv, cwd=build, env=env, stdout=stdout, stderr=subprocess.STDOUT if stdout else None)
        while True:
            try:
                status = proc.wait(timeout=0.25)
                return status if status >= 0 else 128 - status
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    proc.kill()
                    proc.wait(timeout=5)
                    return 128 + interrupted

    try:
        with (output / 'run.log').open('w') as log:
            status = execute([*command, *options, f'--srcdir={source}', f'--tooldir={build}',
                              f'--rsync-bin={binary}', f'--profiles={profiles}',
                              f'--receipt={output / "receipt.json"}'], log)
        with (output / 'run.log').open(errors='replace') as log:
            shutil.copyfileobj(log, sys.stdout)
        if interrupted is not None:
            return 128 + interrupted
        receipt = output / 'receipt.json'
        if not receipt.is_file() or not receipt.stat().st_size:
            print(f'Missing test receipt: {receipt}', file=sys.stderr)
            return 2
        verified = execute([*command, '--check-receipt', str(receipt),
                            f'--rsync-bin={binary}', f'--profiles={profiles}', *fixed_options()])
        if interrupted is not None:
            return 128 + interrupted
        if verified > 1:
            return verified
        if verified != status:
            print('Test receipt disagrees with the runner exit status', file=sys.stderr)
            return 2
        return status
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except OSError as error:
        print(f'Check failed: {error}', file=sys.stderr)
        raise SystemExit(2)
