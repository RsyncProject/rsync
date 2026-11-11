#!/usr/bin/env python3

from contextlib import redirect_stderr
import errno
import fcntl
import hashlib
from io import StringIO
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from harness.process import quote_command
from harness import Exit, TestContext, requires, run, runner
from harness.results import test_error
from harness.results import test_fail
from harness.rsync import rsync_command_binary

@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    recipe = context.repository / 'testsuite' / 'tools' / 'check.py'
    if not recipe.is_file():
        test_fail('shared check recipe is missing')
    binary = rsync_command_binary()
    output = context.scratch / 'native run'
    command = [sys.executable, '-S', str(recipe), str(context.tools), binary, 'pipe,self-peer', str(output)]
    env = os.environ.copy()
    env['whichtests'] = 'must-not-run'
    env['PYTHONUNBUFFERED'] = '1'

    def invoke(args, success, message=None, timeout=180, input=None):
        print(f'recipe: {quote_command(args)}', flush=True)
        cancelled = False

        def cancel(signum, frame):
            nonlocal cancelled
            cancelled = True

        previous = signal.signal(signal.SIGTERM, cancel)
        proc = None
        try:
            proc = subprocess.Popen(args, env=env, cwd=context.scratch, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, stdin=subprocess.PIPE if input is not None else None,
                                    text=True, start_new_session=True)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if cancelled or remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    stdout, stderr = proc.communicate(input=input, timeout=min(remaining, 0.25))
                    break
                except subprocess.TimeoutExpired:
                    input = None
                    continue
        except BaseException as error:
            stdout = stderr = ''
            if proc is not None:
                # Let the runner stop its own test groups before forcing exit
                for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                    try:
                        os.killpg(proc.pid, sig)
                    except ProcessLookupError:
                        pass
                    try:
                        stdout, stderr = proc.communicate(timeout=grace)
                    except subprocess.TimeoutExpired:
                        if sig == signal.SIGKILL:
                            raise
            if isinstance(error, subprocess.TimeoutExpired):
                reason = 'cancelled' if cancelled else f'timed out after {timeout} seconds'
                diagnostic = f'recipe {reason}: {quote_command(args)}\n{stdout}{stderr}'
                output = Path(args[6])
                for path in [output / 'run.log', *sorted(output.glob('scratch/testtmp/*/test.log'))]:
                    try:
                        with path.open('rb') as log:
                            log.seek(0, os.SEEK_END)
                            log.seek(max(0, log.tell() - 65536))
                            diagnostic += f'\n{path.relative_to(output)} (tail):\n' + log.read(65536).decode('utf-8', 'replace')
                    except FileNotFoundError:
                        continue
                test_error(diagnostic)
            raise
        finally:
            signal.signal(signal.SIGTERM, previous)
            if proc is not None:
                proc.stdout.close()
                proc.stderr.close()
        result = subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        if (result.returncode == 0) != success:
            test_fail(f'wrong recipe result: {result.returncode}\n{result.stdout}{result.stderr}')
        if message and message not in result.stdout + result.stderr:
            test_fail(f'missing recipe diagnostic: {message}\n{result.stdout}{result.stderr}')
        return result

    invoke(command + ['cli-transfers', '-j2'], True)
    receipt = json.loads((output / 'receipt.json').read_text())
    identity = receipt['run']
    digest = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    if (identity['primary_sha256'] != digest or identity['secondary_sha256'] != digest
            or identity['primary_version'] != identity['secondary_version']
            or identity['archived_role'] is not None
            or identity['euid'] != os.geteuid() or identity['selected'] != ['cli-transfers']
            or receipt['summary']['verdicts'] != {'pass': 1}):
        test_fail('recipe receipt identifies the wrong run')
    if identity['source_revision'] is not None and not isinstance(identity['source_dirty'], bool):
        test_fail('source revision has no working-tree state')
    if (context.repository / '.git').exists() and shutil.which('git'):
        git = ['git', '--no-optional-locks', '-c', f'safe.directory={context.repository.resolve()}',
               '-C', str(context.repository)]
        revision = subprocess.run(git + ['rev-parse', 'HEAD'], capture_output=True, text=True, timeout=30)
        status = subprocess.run(git + ['status', '--porcelain'], capture_output=True, text=True, timeout=30)
        expected = (None, None)
        if not revision.returncode and not status.returncode:
            expected = revision.stdout.strip(), bool(status.stdout)
        if (identity['source_revision'], identity['source_dirty']) != expected:
            test_fail('receipt identifies the wrong source checkout')
    checkout = context.scratch / 'checkout'
    (checkout / '.git').mkdir(parents=True)
    for error in (FileNotFoundError('git'), subprocess.CalledProcessError(1, 'git'),
                  subprocess.TimeoutExpired('git', 30)):
        with patch.object(runner.subprocess, 'run', side_effect=error):
            if runner.source_identity(checkout) != (None, None):
                test_fail('unavailable Git metadata was accepted')
    if not (output / 'run.log').is_file() or not (output / 'scratch' / 'testtmp').is_dir():
        test_fail('recipe did not isolate its log and scratch')
    original = (output / 'receipt.json').read_bytes()
    invoke(command + ['cli-transfers'], False)
    if (output / 'receipt.json').read_bytes() != original:
        test_fail('recipe overwrote an existing result')

    def attempt(name, binary_path=binary, profiles='pipe,self-peer', extra=()):
        return [sys.executable, '-S', str(recipe), str(context.tools), binary_path, profiles,
                str(context.scratch / name), 'cli-transfers', *extra]

    invoke(attempt('missing-binary', str(context.scratch / 'absent')), False, 'Not an executable file:')
    wrong = context.scratch / 'wrong-rsync'
    wrong.write_text(f'#!{sys.executable}\nraise SystemExit(1)\n')
    wrong.chmod(0o755)
    invoke(attempt('wrong-binary', str(wrong)), False, 'cannot determine rsync version')
    invoke(attempt('no-profile', profiles=''), False, 'Usage: check.py')
    invoke(attempt('empty-profiles', profiles=' , '), False, 'no profiles selected')
    invoke(attempt('no-tests', extra=('--exclude=*',)), False, 'no tests selected')
    invoke(attempt('fixed-inputs', extra=(f'--receipt={context.scratch / "elsewhere.json"}',
                                        f'--rsync-bin={wrong}', '--profiles=')), True)
    fixed = json.loads((context.scratch / 'fixed-inputs' / 'receipt.json').read_text())
    if fixed['run']['primary_sha256'] != digest or fixed['run']['profiles'] != ['pipe', 'self-peer']:
        test_fail('runner arguments changed the recipe inputs')
    invoke([sys.executable, '-S', str(recipe), str(context.tools), binary, 'pipe,self-peer',
            str(context.scratch / 'long-parent' / ('directory' * 15)), 'daemon-unix-socket-atfd'], True)
    if os.geteuid() == 0 and sys.platform == 'linux':
        private = context.scratch / 'private-home'
        build = private / 'build'
        build.mkdir(parents=True)
        shutil.copy2(binary, build / 'rsync')
        for name in ('tls', 'trimslash', 't_unsafe', 't_chmod_secure', 't_secure_relpath',
                     'wildtest', 'getgroups', 'getfsdev', 'config.h', 'shconfig'):
            shutil.copy2(context.tools / name, build / name)
        private.chmod(0o700)
        os.chown(private, 65534, 65534)
        invoke([sys.executable, '-S', str(recipe), str(build), str(build / 'rsync'), 'pipe,self-peer',
                str(private / 'results'), 'partial-no-write'], True)
    blocked = context.scratch / 'not-a-directory'
    blocked.touch()
    invoke(attempt('not-a-directory/result'), False)
    fixture = context.scratch / 'fixture'
    tools = fixture / 'testsuite' / 'tools'
    tests = fixture / 'testsuite' / 'tests'
    tools.mkdir(parents=True)
    tests.mkdir()
    shutil.copyfile(recipe, tools / 'check.py')
    (fixture / 'testsuite' / 'runtests.py').write_text('raise SystemExit(0)\n')
    invoke([sys.executable, '-S', str(tools / 'check.py'), str(context.tools), binary, 'pipe,self-peer',
            str(context.scratch / 'missing-receipt')], False, 'Missing test receipt:')
    shutil.copyfile(context.repository / 'testsuite' / 'runtests.py', fixture / 'testsuite' / 'runtests.py')
    (tests / 'first.py').write_text('raise SystemExit(1)\n')
    (tests / 'second.py').write_text('raise SystemExit(0)\n')
    profile = fixture / 'profile.json'
    profile.write_text('{}\n')
    (tests / 'stdin.py').write_text('import sys\nassert sys.stdin.read() == "retained input"\n')
    invoke([sys.executable, '-S', str(tools / 'check.py'), str(context.tools), binary,
            str(profile), str(context.scratch / 'stdin'), 'stdin'], True, input='retained input')
    (tests / 'stdin.py').unlink()
    output = context.scratch / 'incomplete'
    result = invoke([sys.executable, '-S', str(tools / 'check.py'), str(context.tools), binary,
                     str(profile), str(output), '--stop-on-fail'], False)
    receipt = json.loads((output / 'receipt.json').read_text())
    if (result.returncode != 1 or receipt['run']['selected'] != ['first', 'second']
            or len(receipt['tests']) != 1 or not receipt['summary']['mismatches']):
        test_fail('an incomplete run lost its failure evidence')
    if receipt['run']['source_revision'] is not None or receipt['run']['source_dirty'] is not None:
        test_fail('a non-checkout source acquired its parent revision')

    (fixture / 'testsuite' / 'runtests.py').write_text(
        'import sys\nassert sys.flags.no_site\nfrom harness import runner\nrunner.main()\n')
    invoke([sys.executable, '-S', str(tools / 'check.py'), str(context.tools), binary,
            str(profile), str(context.scratch / 'no-site'), 'second'], True)

    (fixture / 'testsuite' / 'runtests.py').write_text(
        "import sys\nfrom pathlib import Path\nfrom harness import runner\n"
        "if '--check-receipt' not in sys.argv:\n"
        "    runner.write_receipt = lambda path, data: Path(path).write_text('invalid receipt')\n"
        "runner.main()\n")
    invoke([sys.executable, '-S', str(tools / 'check.py'), str(context.tools), binary,
            str(profile), str(context.scratch / 'invalid-receipt'), 'second'], False,
           'invalid test receipt')

    (tools / 'check.py').write_text(recipe.read_text().replace(
        'import sys\n', 'import sys\nprint("outer stdout", flush=True)\n'
        'print("outer stderr", file=sys.stderr, flush=True)\n', 1))
    (fixture / 'testsuite' / 'runtests.py').write_text(
        'import fcntl, os, signal, sys\nfrom pathlib import Path\nfrom harness import runner\n'
        'output = Path(next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--receipt="))).parent\n'
        'held = (output / "runner.lock").open("w")\nfcntl.lockf(held, fcntl.LOCK_EX)\n'
        '(output / "runner.pid").write_text(str(os.getpid()))\n'
        # Force worker-thread delivery where signal masks are available
        'if hasattr(signal, "pthread_sigmask"):\n'
        '    original = runner.run_one_test\n'
        '    def unblocked(*args, **kwargs):\n'
        '        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})\n'
        '        return original(*args, **kwargs)\n'
        '    runner.run_one_test = unblocked\n'
        '    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})\n'
        'runner.main()\n')
    stalled = (
        'import fcntl, os, signal\nfrom pathlib import Path\n'
        'root = Path(os.environ["scratchdir"])\n'
        'held = (root / "test.lock").open("w")\nfcntl.lockf(held, fcntl.LOCK_EX)\n'
        '(root / "test.pid").write_text(str(os.getpid()))\n'
        'print("test ready", flush=True)\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        'if os.fork() == 0:\n'
        '    held.close()\n'
        '    held = (root / "child.lock").open("w")\n'
        '    fcntl.lockf(held, fcntl.LOCK_EX)\n'
        '    print("child ready", flush=True)\n    (root / "ready").touch()\n'
        'signal.pause()\n')
    for name in ('first', 'second'):
        (tests / f'{name}.py').write_text(stalled)

    def locked(path):
        with path.open('r+') as held:
            try:
                fcntl.lockf(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    return True
                raise
        return False

    communicate = subprocess.Popen.communicate
    clock = time.monotonic
    for parallel in (1, 2):
        output = context.scratch / f'timeout-{parallel}'
        roots = [output / 'scratch' / 'testtmp' / name for name in ('first', 'second')[:parallel]]
        expired = False
        started = clock()

        def expire(proc, input=None, timeout=None):
            nonlocal expired
            if not expired:
                expired = True
                deadline = clock() + 30
                while not all((root / 'ready').is_file() for root in roots):
                    if proc.poll() is not None or clock() >= deadline:
                        raise subprocess.TimeoutExpired(proc.args, timeout)
                    time.sleep(0.01)
                raise subprocess.TimeoutExpired(proc.args, timeout)
            return communicate(proc, input=input, timeout=timeout)

        diagnostic = StringIO()
        exit_code = None
        try:
            with patch.object(time, 'monotonic', lambda: started + int(expired)), \
                 patch.object(subprocess.Popen, 'communicate', expire), redirect_stderr(diagnostic):
                try:
                    invoke([sys.executable, '-S', str(tools / 'check.py'), str(context.tools), binary,
                            str(profile), str(output), *[root.name for root in roots], f'-j{parallel}'],
                           False, timeout=0.01)
                except SystemExit as error:
                    exit_code = error.code
            text = diagnostic.getvalue()
            if exit_code != Exit.ERROR:
                test_fail(f'recipe timeout was not reported as an error: {text}')
            if not all(word in text for word in ('timed out', output.name, 'outer stdout',
                                                 'outer stderr', 'run.log', 'test ready', 'child ready')):
                test_fail(f'recipe timeout lost its diagnostics: {text}')
            locks = [output / 'runner.lock', *[root / name for root in roots for name in ('test.lock', 'child.lock')]]
            deadline = time.monotonic() + 5
            while True:
                held = [path for path in locks if locked(path)]
                if not held:
                    break
                if time.monotonic() >= deadline:
                    test_fail('recipe timeout left nested processes running: '
                              + ', '.join(str(path.relative_to(output)) for path in held) + '\n' + text)
                time.sleep(0.01)
        finally:
            for root in roots:
                if (root / 'test.pid').exists() and any(
                        path.is_file() and locked(path) for path in (root / 'test.lock', root / 'child.lock')):
                    try:
                        os.killpg(int((root / 'test.pid').read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if (output / 'runner.lock').is_file() and locked(output / 'runner.lock'):
                try:
                    os.kill(int((output / 'runner.pid').read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

if __name__ == '__main__':
    run(test)
