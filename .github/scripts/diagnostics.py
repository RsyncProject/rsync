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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'testsuite'))
from harness.check_recipe import check_command


SHELL_NAMES = r'(?:ash|bash|dash|ksh|mksh|sh|zsh)'
SHELL_FILENAME = re.compile(rf'\.{SHELL_NAMES}(?:\.in)?\Z')
SHELL_SHEBANG = re.compile(
    rf'^#![ \t]*(?:[^ \t\r\n]*/)?(?:env[ \t]+(?:-S[ \t]+)?)?{SHELL_NAMES}(?:[ \t\r\n]|$)')
SHEBANG_BYTES = 256


def check_helpers(directory):
    suites = sorted(directory.glob('test-*.py'))
    if not suites:
        raise ValueError('No CI helper tests found')
    for suite in suites:
        print(f'Checking {suite.name}', flush=True)
        status = subprocess.call([sys.executable, '-B', '-S', str(suite)])
        if status:
            return status if status > 0 else 128 - status
    return 0


def check_shell_scripts(directory):
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=directory).split(b'\0')
    scripts = []
    for name in filter(None, paths):
        relative = os.fsdecode(name)
        path = directory / relative
        if path.is_symlink() or not path.is_file():
            continue
        if not SHELL_FILENAME.search(path.name):
            with path.open('rb') as stream:
                shebang = stream.readline(SHEBANG_BYTES).decode('ascii', errors='replace')
            if not SHELL_SHEBANG.match(shebang):
                continue
        scripts.append(relative)
    if not scripts:
        raise ValueError('No tracked shell scripts found')
    print(f'Checking {len(scripts)} tracked shell scripts', flush=True)
    return subprocess.call(['shellcheck', '--', *scripts], cwd=directory)


def check_cppcheck(build):
    # Cppcheck 2.19 loses the build-root include path if run from that directory.
    # The rounding probe deliberately compiles invalid array sizes.
    command = ['cppcheck', '--project=' + str(build / 'compile_commands.json'),
               '-i' + str(build / 'rounding.c'), '--platform=unix64',
               '--library=' + str(Path(__file__).resolve().with_name('cppcheck.cfg')),
               '--enable=warning,performance,portability,missingInclude',
               '--suppress=missingIncludeSystem',
               '--error-exitcode=1', '--quiet', f'-j{len(os.sched_getaffinity(0))}']
    return subprocess.call(command, cwd=build.parent)


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
    sub.add_parser('check-helpers')
    sub.add_parser('check-shell-scripts')
    coverage = sub.add_parser('coverage')
    coverage.add_argument('transport', choices=['pipe', 'tcp'])
    sub.add_parser('coverage-summary')
    scan = sub.add_parser('scan')
    scan.add_argument('llvm_major', type=int)
    sub.add_parser('cppcheck')
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
    if args.task == 'check-helpers':
        return check_helpers(Path(__file__).resolve().parent)
    if args.task == 'check-shell-scripts':
        return check_shell_scripts(Path(__file__).resolve().parents[2])
    if args.task == 'coverage':
        target = 'coverage' if args.transport == 'pipe' else 'coverage-tcp'
        return logged(['make', target], Path(f'cov-{args.transport}.log'))
    if args.task == 'coverage-summary':
        for transport in ('pipe', 'tcp'):
            summary(f'Coverage ({transport})', Path(f'cov-{transport}.log'), r'^(lines|functions|branches|decisions):')
        return 0
    if args.task == 'scan':
        version = args.llvm_major
        command = [f'intercept-build-{version}', f'scan-build-{version}',
                   f'--use-cc=clang-{version}', f'--use-c++=clang++-{version}',
                   '--status-bugs', '-o', str(Path('scan-report').resolve()),
                   'make', 'check-progs', f'-j{len(os.sched_getaffinity(0))}']
        result = logged(command, Path('scan-build.out'))
        summary(f'Clang {version}', Path('scan-build.out'), r'scan-build: .* bugs? found|scan-build: No bugs found')
        return result
    if args.task == 'cppcheck':
        return check_cppcheck(Path.cwd())
    output = Path(os.environ['VALGRIND_SCRATCH'])
    evidence = Path('test-results')
    evidence.mkdir(exist_ok=True)
    if args.task == 'valgrind':
        if not args.profiles:
            parser.error('--profiles is required for Valgrind tests')
        command = check_command('.', './rsync', args.profiles, output, args.transport,
                                '--valgrind', '--valgrind-opts=--leak-check=no --error-exitcode=0',
                                '-j2', '--timeout=600', '--preserve-scratch')
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
