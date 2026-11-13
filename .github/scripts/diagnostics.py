import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request


def logged(command, path):
    with path.open('w') as log, subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               text=True, errors='replace') as proc:
        def cancel(signum, frame):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            raise SystemExit(128 + signum)
        previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            for line in proc.stdout:
                log.write(line)
                print(line, end='', flush=True)
            return proc.wait()
        finally:
            proc.stdout.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def summary(title, path, pattern):
    lines = []
    if path.is_file():
        with path.open(errors='replace') as stream:
            lines = [line.rstrip() for line in stream if re.search(pattern, line)]
    text = f'## {title}\n\n```text\n' + '\n'.join(lines or ['No summary produced']) + '\n```\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(text)


def install_actionlint():
    name = 'actionlint_1.7.12_linux_amd64.tar.gz'
    url = f'https://github.com/rhysd/actionlint/releases/download/v1.7.12/{name}'
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            break
        except (OSError, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(3 * (attempt + 1))
    if hashlib.sha256(data).hexdigest() != '8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8':
        raise ValueError('actionlint SHA-256 mismatch')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        member = archive.getmember('actionlint')
        if not member.isfile():
            raise ValueError('actionlint archive has no regular executable')
        with archive.extractfile(member) as source, Path('actionlint').open('wb') as destination:
            shutil.copyfileobj(source, destination)
    Path('actionlint').chmod(0o755)
    with open(os.environ['GITHUB_PATH'], 'a') as stream:
        stream.write(str(Path.cwd()) + '\n')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='task', required=True)
    sub.add_parser('install-actionlint')
    coverage = sub.add_parser('coverage')
    coverage.add_argument('transport', choices=['pipe', 'tcp'])
    sub.add_parser('coverage-summary')
    sub.add_parser('scan')
    for name in ('valgrind', 'valgrind-results'):
        command = sub.add_parser(name)
        command.add_argument('--privilege', choices=['root', 'non-root'], required=True)
        command.add_argument('--transport', choices=['pipe', 'tcp'], required=True)
        command.add_argument('--profiles')
    args = parser.parse_args()
    if args.task.startswith('valgrind') and args.privilege == 'root' and os.geteuid() != 0:
        return subprocess.call(['sudo', '-E', sys.executable, '-S', __file__, *sys.argv[1:]])
    if args.task == 'install-actionlint':
        install_actionlint()
        return 0
    if args.task == 'coverage':
        target = 'coverage' if args.transport == 'pipe' else 'coverage-tcp'
        return logged(['make', target], Path(f'cov-{args.transport}.log'))
    if args.task == 'coverage-summary':
        for transport in ('pipe', 'tcp'):
            summary(f'Coverage ({transport})', Path(f'cov-{transport}.log'), r'^(lines|functions|branches|decisions):')
        return 0
    if args.task == 'scan':
        command = ['scan-build-21', '--status-bugs', '-o', str(Path('scan-report').resolve()),
                   'make', 'check-progs', f'-j{len(os.sched_getaffinity(0))}']
        result = logged(command, Path('scan-build.out'))
        summary('Clang 21', Path('scan-build.out'), r'scan-build: .* bugs? found|scan-build: No bugs found')
        return result
    output = Path(os.environ['VALGRIND_SCRATCH'])
    evidence = Path('test-results')
    evidence.mkdir(exist_ok=True)
    if args.task == 'valgrind':
        if not args.profiles:
            parser.error('--profiles is required for Valgrind tests')
        options = ['--use-tcp', '--daemon-tests-only'] if args.transport == 'tcp' else []
        command = [sys.executable, '-S', 'testsuite/tools/check.py', '.', './rsync', args.profiles, str(output),
                   '--valgrind', '--valgrind-opts=--leak-check=no --error-exitcode=0',
                   *options, '-j2', '--timeout=600', '--preserve-scratch']
        result = logged(command, evidence / f'valgrind-{args.privilege}-{args.transport}.log')
        if (output / 'receipt.json').is_file():
            shutil.copyfile(output / 'receipt.json', evidence / f'valgrind-{args.privilege}-{args.transport}.json')
        return result
    logs = sorted((output / 'scratch' / 'testtmp' / 'valgrind-logs').glob('valgrind.*.log'))
    if not logs:
        raise ValueError('No Valgrind logs were produced')
    destination = evidence / 'valgrind-logs'
    destination.mkdir(exist_ok=True)
    failed = []
    for path in logs:
        shutil.copyfile(path, destination / path.name)
        with path.open(errors='replace') as stream:
            if any(re.search(r'ERROR SUMMARY: [1-9][0-9]* errors', line) for line in stream):
                failed.append(path.name)
    print(f'Scanned {len(logs)} Valgrind logs')
    if failed:
        raise ValueError('Valgrind reported errors: ' + ', '.join(failed))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Diagnostic check failed: {error}', file=sys.stderr)
        raise SystemExit(1)
