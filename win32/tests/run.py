#!/usr/bin/env python3
"""Run the Windows test suite.

    python win32/tests/run.py --rsync-bin build/rsync.exe [TEST ...]

Each test is a test_*.py in this directory, run in its own scratch
directory and reporting PASS (0), FAIL (1) or SKIP (77).  Skips are
expected here: they mark what Windows genuinely cannot do rather than
something being broken.

This exists alongside testsuite/, not instead of it.  That suite assumes
Unix semantics throughout, so rather than editing shared test code to suit
one platform, this covers what the Windows port supports.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASS, FAIL, SKIP = 0, 1, 77


def rmtree(path):
    def force_writable(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    for _ in range(5):
        if not os.path.exists(path):
            return
        try:
            shutil.rmtree(path, onerror=force_writable)
            return
        except OSError:
            time.sleep(0.2)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('tests', nargs='*', metavar='TEST',
                    help='test names or globs (default: all)')
    ap.add_argument('--rsync-bin', required=True,
                    help='the rsync executable to test')
    ap.add_argument('--tooldir',
                    help='where the C helpers live (default: rsync-bin dir)')
    ap.add_argument('--srcdir', default=str(HERE.parent.parent),
                    help='the rsync source tree')
    ap.add_argument('--scratch',
                    help='scratch root (default: <tooldir>/wintesttmp)')
    ap.add_argument('--keep-scratch', action='store_true',
                    help='keep scratch dirs for inspection')
    args = ap.parse_args()

    rsync_bin = Path(args.rsync_bin).resolve()
    if not rsync_bin.is_file():
        sys.stderr.write(f"no such rsync binary: {rsync_bin}\n")
        return 2

    tooldir = Path(args.tooldir).resolve() if args.tooldir else rsync_bin.parent
    srcdir = Path(args.srcdir).resolve()
    scratch_root = Path(args.scratch).resolve() if args.scratch \
        else tooldir / 'wintesttmp'

    scripts = sorted(HERE.glob('test_*.py'))
    if args.tests:
        wanted = []
        for script in scripts:
            name = script.stem[len('test_'):]
            if any(fnmatch.fnmatch(name, pat) for pat in args.tests):
                wanted.append(script)
        scripts = wanted
        if not scripts:
            sys.stderr.write(f"no tests match {args.tests}\n")
            return 2

    print('=' * 62)
    print(f'rsync Windows test suite')
    print(f'    rsync   = {rsync_bin}')
    print(f'    tooldir = {tooldir}')
    print(f'    srcdir  = {srcdir}')
    print(f'    scratch = {scratch_root}')
    print('=' * 62)

    passed = failed = skipped = 0
    failures = []

    for script in scripts:
        name = script.stem[len('test_'):]
        scratch = scratch_root / name
        rmtree(scratch)
        scratch.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            WINTEST_RSYNC=str(rsync_bin),
            WINTEST_TOOLDIR=str(tooldir),
            WINTEST_SRCDIR=str(srcdir),
            WINTEST_SCRATCH=str(scratch),
            PYTHONPATH=str(HERE) + os.pathsep + env.get('PYTHONPATH', ''),
        )
        # Drop rsync's own env knobs so a caller's settings can't skew a
        # run.  They must be removed, not blanked: rsync treats an empty
        # RSYNC_RSH as a command, and then argv[0] is whatever came next.
        for var in ('RSYNC_RSH', 'RSYNC_OLD_ARGS', 'RSYNC_PROTECT_ARGS',
                    'RSYNC_CHECKSUM_LIST', 'RSYNC_COMPRESS_LIST'):
            env.pop(var, None)

        logfile = scratch / 'test.log'
        # A test that hangs is a failure of that test, not of the run: report
        # it and carry on, rather than letting TimeoutExpired escape and take
        # every remaining test with it.  rsync deadlocks are exactly the kind
        # of bug this suite exists to catch, so this path is not theoretical.
        with open(logfile, 'w', encoding='utf-8', errors='replace') as log:
            try:
                proc = subprocess.run([sys.executable, str(script)],
                                      stdout=log, stderr=subprocess.STDOUT,
                                      env=env, cwd=str(scratch), timeout=300)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                log.write('\nTIMEOUT: the test did not finish within 300s\n')
                returncode = FAIL
                # Whatever it was waiting on is still running; without this
                # the scratch dir cannot be removed and the stray rsync
                # lingers after the suite exits.
                for stray in ('rsync.exe', 'rsync'):
                    subprocess.run(['taskkill', '/F', '/IM', stray],
                                   capture_output=True)

        if returncode == PASS:
            print(f'PASS   {name}')
            passed += 1
            if not args.keep_scratch:
                rmtree(scratch)
        elif returncode == SKIP:
            reason = ''
            for line in logfile.read_text(encoding='utf-8',
                                          errors='replace').splitlines():
                if line.startswith('SKIP: '):
                    reason = line[len('SKIP: '):]
            print(f'SKIP   {name}' + (f' ({reason})' if reason else ''))
            skipped += 1
            if not args.keep_scratch:
                rmtree(scratch)
        else:
            print(f'FAIL   {name}')
            print('----- log follows')
            print(logfile.read_text(encoding='utf-8', errors='replace').rstrip())
            print('----- log ends')
            failed += 1
            failures.append(name)

    print('-' * 62)
    print('----- overall results:')
    print(f'      {passed} passed')
    if failed:
        print(f'      {failed} failed')
    if skipped:
        print(f'      {skipped} skipped')
    print('-' * 62)
    if failures:
        print('failed: ' + ', '.join(failures))
    return FAIL if failed else PASS


if __name__ == '__main__':
    sys.exit(main())
