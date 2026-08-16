#!/usr/bin/env python3

# Copyright (C) 2001, 2002 by Martin Pool <mbp@samba.org>
# Copyright (C) 2003-2022 Wayne Davison
# Copyright (C) 2026 Andrew Tridgell
#
# Rewrite of runtests.sh in Python (runtests.sh is now deprecated).
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version
# 2 as published by the Free Software Foundation.

"""rsync test runner.

Invokes test scripts from testsuite/ and reports results.
Can be called by 'make check' or directly.

Usage:
    ./runtests.py [options] [TEST ...]

Each TEST is a test name (e.g. 'delete') or glob pattern (e.g. 'xattr*').
If no tests are specified, all tests are run.
"""

import argparse
import concurrent.futures
import fnmatch
import glob
import math
import os
import signal
import subprocess
import sys
import threading
import time

# Share the test exit-code enum with the test helpers. exitcodes.py lives in
# testsuite/ (next to this script); it has no import-time side effects.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testsuite'))
from exitcodes import Exit


def _race_seconds(text):
    """argparse type for --race-timeout: a finite, strictly positive number.

    A race test loops `while monotonic() < deadline`, so a budget of 0 (or a
    negative, or a NaN, which fails every comparison) runs the body ZERO times
    and the test reports PASS without ever exercising its oracle -- a silently
    disarmed security test, which is worse than a slow one. Infinity would run
    until the unrelated per-test timeout. The old max(RACE_TIMEOUT, 10.0) floor
    used to make this unreachable; validating here restores that guarantee."""
    try:
        secs = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not a number: {text!r}')
    if not math.isfinite(secs) or secs <= 0:
        raise argparse.ArgumentTypeError(
            f'must be a finite positive number of seconds, got {text!r}; '
            'a zero/negative/NaN budget would make every race test pass '
            'without running its race')
    return secs


def parse_args():
    p = argparse.ArgumentParser(description='Run rsync test suite')
    p.add_argument('tests', nargs='*', metavar='TEST',
                   help='Test names or patterns to run (default: all)')
    p.add_argument('--exclude', default=None, metavar='LIST',
                   help='Comma-separated test names/globs to skip entirely: '
                        'they are not run and not reported as skipped. Useful '
                        'for tests that cannot work in a given build/CI '
                        'environment (e.g. a restricted buildd chroot). '
                        'Falls back to the RSYNC_EXCLUDE environment variable.')
    p.add_argument('-j', '--parallel', type=int, default=1, metavar='N',
                   help='Run up to N tests in parallel (default: 1)')
    p.add_argument('--valgrind', action='store_true',
                   help='Run rsync under valgrind (logs to per-process files)')
    p.add_argument('--valgrind-opts', default='', metavar='OPTS',
                   help='Extra valgrind options (e.g. "--leak-check=full")')
    p.add_argument('--preserve-scratch', action='store_true',
                   help='Keep scratch directories after tests complete')
    p.add_argument('--log-level', type=int, default=1, metavar='N',
                   help='Verbosity level 1-10 (default: 1)')
    p.add_argument('--always-log', action='store_true',
                   help='Show test logs even for passing tests')
    p.add_argument('--stop-on-fail', action='store_true',
                   help='Stop after first test failure')
    p.add_argument('--timing', action='store_true',
                   help='After the run, report each test\'s wall-clock time, '
                        'slowest first. With -j N the report also shows how '
                        'much of the run the slowest test alone accounts for.')
    p.add_argument('--timeout', type=int, default=300, metavar='SECS',
                   help='Per-test timeout in seconds (default: 300)')
    p.add_argument('--race-timeout', type=_race_seconds, default=None, metavar='SECS',
                   help='Budget (seconds) a TOCTOU symlink-race test may spend '
                        'trying to win its race before concluding. Overrides '
                        'every such test\'s own default (5-15s, the suite\'s '
                        'slowest tests: a race test always spends its whole '
                        'budget). Lowering it speeds the suite up but weakens '
                        'the oracle. Unset: each test keeps its default.')
    p.add_argument('--rsync-bin', default=None, metavar='PATH',
                   help='Path to rsync binary (default: ./rsync)')
    p.add_argument('--rsync-bin2', default=None, metavar='PATH',
                   help='Path to a second ("peer") rsync binary used for the '
                        'daemon side and remote-shell --rsync-path. Lets the '
                        'suite mix two rsync versions over the wire. Default: '
                        'same as --rsync-bin (no version mixing).')
    p.add_argument('--tooldir', default=None, metavar='DIR',
                   help='Tool/build directory (default: cwd)')
    p.add_argument('--srcdir', default=None, metavar='DIR',
                   help='Source directory (default: script directory)')
    p.add_argument('--protocol', type=int, default=None, metavar='VER',
                   help='Force protocol version (adds --protocol=VER to rsync)')
    p.add_argument('--expect-skipped', default=None, metavar='LIST',
                   help='Comma-separated list of expected-skipped tests. An '
                        '@FILE entry reads a skip list (one test per line, '
                        '"#" comments); relative paths resolve against srcdir '
                        'and several may be composed, e.g. '
                        '@testsuite/skiplist/linux.txt,@testsuite/skiplist/proto29.txt. '
                        'A -NAME entry removes a name the rest of the spec '
                        'added, for a host that can really run a test its '
                        'platform list expects to skip.')
    p.add_argument('--expect-result', default=None, metavar='FILE',
                   help='Path to an expected-outcome manifest (one '
                        '"<testname> <pass|skip|fail|xfail>" per line). When '
                        'set, ONLY the tests listed in FILE are run, and each '
                        "test's actual outcome is compared against its "
                        'expected one; any mismatch (including an unexpected '
                        'pass) fails the run. Used for version-mixing CI.')
    p.add_argument('--daemon-tests-only', action='store_true',
                   help='Run only the tests that can reach the daemon '
                        'transport. Intended for a --use-tcp pass that follows '
                        'a full default-transport run: the tests this drops '
                        'never call start_test_daemon(), so they cannot observe '
                        '--use-tcp and would just repeat themselves. Disables '
                        'the expected-skip oracle (it describes a full run).')
    p.add_argument('--use-tcp', action='store_true',
                   help='Run daemon tests against a real rsyncd bound to '
                        '127.0.0.1 (non-default). The default is the secure '
                        'stdio-pipe transport, which opens no listening '
                        'socket; --use-tcp exposes a loopback port for the '
                        'duration of each daemon test.')
    return p.parse_args()


def find_setfacl_nodef(scratchbase):
    """Determine the setfacl command to remove default ACLs."""
    for cmd in [
        ['setacl', '-k', 'u::7,g::5,o:5', scratchbase],
        ['setfacl', '-k', scratchbase],
        ['setfacl', '-s', 'u::7,g::5,o:5', scratchbase],
    ]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
            return cmd[:2] if cmd[0] == 'setacl' else cmd[:2]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    try:
        r = subprocess.run(['setfacl', '--help'], capture_output=True, text=True, timeout=5)
        if '-k,' in r.stdout or '-k,' in r.stderr:
            return ['setfacl', '-k']
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_tls_args(config_h):
    """Determine TLS_ARGS from config.h."""
    args = ''
    try:
        with open(config_h) as f:
            text = f.read()
        if '#define HAVE_LUTIMES 1' in text:
            args += ' -l'
        if '#undef CHOWN_MODIFIES_SYMLINK' in text:
            args += ' -L'
    except FileNotFoundError:
        pass
    return args.strip()


def read_shconfig(path):
    """Read shell config variables from shconfig."""
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or line.startswith('export') or not line:
                    continue
                if '=' in line:
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return env


def get_testuser():
    """Determine the current test user."""
    for cmd in ['/usr/bin/whoami', '/usr/ucb/whoami', '/bin/whoami']:
        if os.path.isfile(cmd):
            try:
                return subprocess.check_output([cmd], text=True).strip()
            except subprocess.CalledProcessError:
                pass
    try:
        return subprocess.check_output(['id', '-un'], text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return os.environ.get('LOGNAME', os.environ.get('USER', 'UNKNOWN'))


def _move_aside(path):
    """Rename an un-removable directory to a unique sibling so its name is free.

    A rename-storm symlink-race test can corrupt a directory on some filesystems
    (OpenBSD FFS soft-updates can leave an "empty" dir that still reports
    ENOTEMPTY/EPERM and only fsck clears). `rm -rf` then can't remove it, but
    renaming the top dir aside succeeds even with a corrupted descendant, freeing
    the original name for a clean scratchdir."""
    n = 0
    while os.path.exists(f"{path}.corrupt.{os.getpid()}.{n}"):
        n += 1
    try:
        os.rename(path, f"{path}.corrupt.{os.getpid()}.{n}")
    except OSError:
        pass


def prep_scratch(scratchdir, srcdir, tooldir, setfacl_nodef):
    """Prepare a scratch directory for a test."""
    if os.path.isdir(scratchdir):
        subprocess.run(['chmod', '-R', 'u+rwX', scratchdir], capture_output=True)
        subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
        if os.path.isdir(scratchdir):
            _move_aside(scratchdir)   # rm -rf left corrupted debris; don't inherit it
    os.makedirs(scratchdir, exist_ok=True)
    if setfacl_nodef:
        subprocess.run(setfacl_nodef + [scratchdir], capture_output=True)
    try:
        os.chmod(scratchdir, os.stat(scratchdir).st_mode & ~0o2000)  # clear setgid
    except OSError:
        pass
    src_link = os.path.join(scratchdir, 'src')
    if not os.path.exists(src_link):
        if os.path.isabs(srcdir):
            os.symlink(srcdir, src_link)
        else:
            os.symlink(os.path.join(tooldir, srcdir), src_link)


# Python tests are identified by a positive "_test.py" suffix so that
# helper modules (e.g. rsyncfns.py) sit in testsuite/ without being mistaken
# for tests.
_PY_TEST_SUFFIX = '_test.py'


def _is_test_path(path):
    return os.path.basename(path).endswith(_PY_TEST_SUFFIX)


def _testbase(path):
    """Strip the test extension to get the canonical test name."""
    base = os.path.basename(path)
    if base.endswith(_PY_TEST_SUFFIX):
        return base[:-len(_PY_TEST_SUFFIX)]
    return base


def collect_tests(suitedir, patterns):
    """Collect test scripts (_test.py) matching the given patterns."""
    if not patterns:
        candidates = glob.glob(os.path.join(suitedir, '*' + _PY_TEST_SUFFIX))
        tests = sorted(p for p in candidates if _is_test_path(p))
    else:
        seen = set()
        tests = []
        for pat in patterns:
            # Accept either bare name ("mkpath"), explicit extension, or glob.
            if pat.endswith('.py'):
                pats = [pat]
            else:
                pats = [pat + _PY_TEST_SUFFIX]
            for p in pats:
                for m in sorted(glob.glob(os.path.join(suitedir, p))):
                    if _is_test_path(m) and m not in seen:
                        seen.add(m)
                        tests.append(m)
    return tests


# Tokens through which a test can reach the daemon transport. --use-tcp works by
# setting RSYNC_TEST_USE_TCP, which is read in exactly one place (rsyncfns
# USE_TCP) and acted on in exactly one function (start_test_daemon): a test whose
# source mentions none of these never gets there, so it behaves identically with
# and without --use-tcp and running it a second time under TCP buys no coverage.
#
# The list is the closure of every rsyncfns helper that reaches USE_TCP,
# start_rsyncd or claim_ports, plus the helper modules that open a daemon
# connection themselves and the bare literals a test might use directly. It is
# deliberately over-broad: a false positive only costs runtime, while a false
# negative would silently drop real coverage.
_DAEMON_API = (
    'USE_TCP', 'require_tcp', 'start_test_daemon', 'start_rsyncd',
    'claim_ports', 'claim_free_port', 'setup_chroot_inner',
    'stdio_daemon', 'rsync_proto', 'DaemonClient',
    'rsync://', '--daemon', 'rsyncd',
)


def select_daemon_tests(tests):
    """Split `tests` into (daemon-transport tests, the rest).

    Used by --daemon-tests-only so a TCP pass need not re-run the whole suite.
    A test we cannot read is kept, not dropped -- the failure mode of this
    filter must always be "ran too much"."""
    keep, dropped = [], []
    for path in tests:
        try:
            with open(path, errors='replace') as f:
                text = f.read()
        except OSError:
            keep.append(path)
            continue
        (keep if any(tok in text for tok in _DAEMON_API) else dropped).append(path)
    return keep, dropped


_VALID_OUTCOMES = ('pass', 'skip', 'fail', 'xfail')


def parse_expect_result(path):
    """Parse an expected-outcome manifest into {testbase: outcome}.

    One "<testname> <outcome>" entry per line; '#' comments and blank lines
    are ignored. outcome is one of pass|skip|fail|xfail. The set of listed
    tests doubles as the run set (see main()). Exits 2 on a malformed file.
    """
    expect = {}
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) != 2 or fields[1] not in _VALID_OUTCOMES:
                sys.stderr.write(
                    f"{path}:{lineno}: expected '<testname> "
                    f"<{'|'.join(_VALID_OUTCOMES)}>', got: {raw.rstrip()}\n"
                )
                sys.exit(Exit.ERROR)
            expect[fields[0]] = fields[1]
    return expect


def expand_skip_spec(spec, srcdir, suitedir):
    """Expand an RSYNC_EXPECT_SKIPPED spec into a normalised csv.

    The spec is a comma-separated list of test names, '@FILE' skip-list
    references, and '-name' removals.  A skip-list file holds one test name per line ('#' starts a
    comment; blank lines are ignored), which is what keeps two branches from
    colliding: adding a test edits one line of one file rather than a shared
    3 KB csv.  Several may be composed, e.g.
        RSYNC_EXPECT_SKIPPED=@testsuite/skiplist/linux.txt,@.../proto29.txt
    Relative paths resolve against srcdir (not the cwd) so out-of-tree builds
    and `make installcheck` work.  A '-name' entry removes a name the rest of
    the spec added, for a host that can genuinely run a test its platform list
    expects to skip; it is applied last, and must actually remove something.

    Entries must name a real test, and each file must be non-empty, sorted and
    free of duplicates: unsorted files defeat the point (everyone appends to
    the same last line), and a stale name would otherwise fail as a skip
    mismatch far from its cause.  Exits 2 on any of those -- nothing malformed
    may quietly shrink the expected set, which would disarm the oracle.
    """
    def die(msg):
        sys.stderr.write(msg + '\n')
        sys.exit(Exit.ERROR)

    # An entirely empty spec is the legitimate "expect no skips at all".  An
    # empty entry *within* a spec is not: it is what an unset shell variable
    # expands to, and silently dropping it would quietly shrink the expected
    # set.
    if not spec.strip():
        return ''

    names = []
    drop = []
    for tok in (t.strip() for t in spec.split(',')):
        if not tok:
            die('RSYNC_EXPECT_SKIPPED: empty entry (an unset variable?): '
                f'{spec!r}')
        if tok.startswith('-'):
            # '-name' removes a name a composed list added, for a host that
            # really can run a test its platform list expects to skip (e.g. a
            # scratch dir on a second filesystem makes a cross-device copy
            # work).  Subtraction cannot be done by whoever composes the spec,
            # because the name lives inside an @FILE that is only expanded
            # here.  Applied after every addition, so order does not matter.
            drop.append((tok[1:], tok))
            continue
        if not tok.startswith('@'):
            names.append((tok, 'RSYNC_EXPECT_SKIPPED'))
            continue
        path = tok[1:]
        if not os.path.isabs(path):
            path = os.path.join(srcdir, path)
        try:
            with open(path) as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError) as e:
            die(f'{tok}: cannot read skip list: {e}')
        prev = None
        found = 0
        for lineno, raw in enumerate(lines, 1):
            name = raw.split('#', 1)[0].strip()
            if not name:
                continue
            where = f'{path}:{lineno}'
            if len(name.split()) != 1:
                die(f'{where}: expected one test name per line, got: {raw.rstrip()}')
            if prev is not None and name <= prev:
                die(f'{where}: skip lists must be sorted and duplicate-free '
                    f'({name!r} follows {prev!r})')
            prev = name
            found += 1
            names.append((name, where))
        # A truncated or emptied list must not read as "expect no skips".
        if not found:
            die(f'{path}: skip list contains no test names')

    seen = {}
    for name, where in names:
        if name in seen:
            continue
        seen[name] = where
        # A plain test name: no path, and no comma (which the expanded csv,
        # and the summary the fleet parses back, use as the separator).
        if ',' in name or '/' in name or os.sep in name or name in ('.', '..'):
            die(f'{where}: not a test name: {name!r}')
        if not os.path.isfile(os.path.join(suitedir, name + '_test.py')):
            die(f'{where}: no such test: {name}')
    for name, tok in drop:
        # Every removal must remove something.  A name the spec never added is
        # stale (the list stopped expecting that skip, or it is misspelled), and
        # a repeated removal is the same no-op written twice.  Shrinking the
        # expected set is precisely what must not happen quietly, so neither is
        # allowed to sit in a config unnoticed.
        if name not in seen:
            die(f'RSYNC_EXPECT_SKIPPED: {tok!r} removes a name that nothing '
                f'added: {name}')
        del seen[name]
    return ','.join(sorted(seen))


def read_backport_exclude(tooldir, suitedir):
    """Test names from the BUILD tree's testsuite/skiplist/backport.txt.

    A stable-backport branch runs a newer suite than its own code, and this
    file names the tests that base cannot run.  It lives in the tree being
    built, not the suite tree, so it cannot be an @FILE in the expect-skipped
    spec -- those resolve against srcdir.
    """
    path = os.path.join(tooldir, 'testsuite', 'skiplist', 'backport.txt')
    if not os.path.isfile(path):
        return set()
    names = set()
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            name = raw.split('#', 1)[0].strip()
            if not name:
                continue
            where = f'{path}:{lineno}'
            if len(name.split()) != 1:
                sys.stderr.write(f'{where}: expected one test name per line\n')
                sys.exit(Exit.ERROR)
            # A stale name here would silently exclude nothing, which is the
            # failure this file exists to prevent.
            if not os.path.isfile(os.path.join(suitedir, name + '_test.py')):
                sys.stderr.write(f'{where}: no such test: {name}\n')
                sys.exit(Exit.ERROR)
            names.add(name)
    return names


_TIMING_TOP = 25


def print_timing_report(durations, outcomes, run_wall, parallel):
    """Report per-test wall-clock, slowest first (--timing).

    With -j N the run cannot finish sooner than its slowest single test, so the
    tail matters as much as the total: a 300s test pins the whole suite to 300s
    no matter how many workers there are. The footer gives both bounds -- the
    serial sum (what one worker would take) and that floor -- so it is obvious
    whether a slow run wants more parallelism or a faster individual test."""
    if not durations:
        return
    ranked = sorted(durations.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(durations.values())
    print(f'----- slowest tests (of {len(ranked)}, wall-clock each):')
    for name, secs in ranked[:_TIMING_TOP]:
        print(f'      {secs:7.1f}s  {name:<32} {outcomes.get(name, "?")}')
    print(f'      serial sum {total:.0f}s over {len(ranked)} tests; '
          f'run took {run_wall:.0f}s with -j{parallel}')
    slowest, slowest_secs = ranked[0]
    if parallel > 1:
        # The floor: even with unlimited workers the suite cannot beat its
        # longest single test.
        print(f'      floor {slowest_secs:.0f}s ({slowest}) = '
              f'{100.0 * slowest_secs / run_wall:.0f}% of this run; '
              f'ideal at -j{parallel} is {total / parallel:.0f}s')


def outcome_of(result):
    """Map a per-test exit code to an outcome string."""
    if result == Exit.PASS:
        return 'pass'
    if result == Exit.SKIP:
        return 'skip'
    if result == Exit.XFAIL:
        return 'xfail'
    return 'fail'


def build_rsync_cmd(rsync_bin, args, scratchbase):
    """Build the RSYNC command string for tests."""
    parts = []
    if args.valgrind:
        # Logs go in a world-writable+sticky subdir so that rsync children
        # which drop privileges (the setpriv cap-drop in partial_nowrite, a
        # daemon dropping to the module's uid) can still create their log file
        # even when scratchbase itself is root-owned.
        vgdir = os.path.join(scratchbase, 'valgrind-logs')
        os.makedirs(vgdir, exist_ok=True)
        os.chmod(vgdir, 0o1777)
        vlog = os.path.join(vgdir, 'valgrind.%p.log')
        vopts = f'--log-file={vlog}'
        supp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'testsuite', 'valgrind.supp')
        if os.path.exists(supp):
            vopts += f' --suppressions={supp}'
        if args.valgrind_opts:
            vopts += ' ' + args.valgrind_opts
        parts.append(f'valgrind {vopts}')
    parts.append(rsync_bin)
    if args.protocol is not None:
        parts.append(f'--protocol={args.protocol}')
    return ' '.join(parts)


class TestResult:
    """Result of a single test execution."""
    __slots__ = ('testbase', 'result', 'output', 'skipped_reason', 'duration')

    def __init__(self, testbase, result, output='', skipped_reason='',
                 duration=0.0):
        self.testbase = testbase
        self.result = result
        self.output = output
        self.skipped_reason = skipped_reason
        self.duration = duration


def run_one_test(testscript, testbase, scratchdir, base_env, timeout,
                 srcdir, tooldir, setfacl_nodef, always_log):
    """Run a single test. Returns a TestResult.

    This function is safe to call from multiple threads — it uses only
    per-test state (unique scratchdir, copy of env).
    """
    started = time.monotonic()
    prep_scratch(scratchdir, srcdir, tooldir, setfacl_nodef)

    env = base_env.copy()
    env['scratchdir'] = scratchdir

    # Dispatch by extension: shell tests via /bin/sh -e, Python tests via
    # the same python3 that's running this runner.
    if testscript.endswith('.py'):
        cmd = [sys.executable, testscript]
    else:
        cmd = ['sh', '-e', testscript]

    logfile = os.path.join(scratchdir, 'test.log')
    with open(logfile, 'w') as log:
        # start_new_session: run the test driver as its own session/group leader
        # so the daemon, clients and flipper it spawns inherit that group. A
        # timeout then killpg's the whole tree (not just the driver), and the
        # lock-file sweep can reap a SIGKILLed run's stranded group the same way.
        proc = subprocess.Popen(
            cmd,
            stdout=log, stderr=subprocess.STDOUT,
            env=env, cwd=env.get('TOOLDIR', '.'),
            start_new_session=True,
        )
        try:
            result = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Reap the whole session group, but only if the driver really is its
            # own group leader (start_new_session took) and that group isn't ours
            # -- killpg of our own group would take down the runner.
            try:
                pgid = os.getpgid(proc.pid)
            except OSError:
                pgid = -1
            if pgid == proc.pid and pgid != os.getpgrp():
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    proc.kill()
            else:
                proc.kill()
            proc.wait()
            result = 1
            log.write(f"\nTIMEOUT: test took over {timeout} seconds\n")

    # Build output text
    output_parts = []

    show_log = always_log or (result not in (Exit.PASS, Exit.SKIP, Exit.XFAIL))
    if show_log:
        output_parts.append(f'----- {testbase} log follows')
        try:
            with open(logfile) as f:
                output_parts.append(f.read().rstrip())
        except FileNotFoundError:
            pass
        output_parts.append(f'----- {testbase} log ends')
        rsyncd_log = os.path.join(scratchdir, 'rsyncd.log')
        if os.path.isfile(rsyncd_log):
            output_parts.append(f'----- {testbase} rsyncd.log follows')
            with open(rsyncd_log) as f:
                output_parts.append(f.read().rstrip())
            output_parts.append(f'----- {testbase} rsyncd.log ends')

    skipped_reason = ''
    if result == Exit.PASS:
        output_parts.append(f'PASS    {testbase}')
    elif result == Exit.SKIP:
        whyfile = os.path.join(scratchdir, 'whyskipped')
        try:
            with open(whyfile) as f:
                skipped_reason = f.read().strip()
        except FileNotFoundError:
            pass
        output_parts.append(f'SKIP    {testbase} ({skipped_reason})')
    elif result == Exit.XFAIL:
        output_parts.append(f'XFAIL   {testbase}')
    else:
        output_parts.append(f'FAIL    {testbase}')

    return TestResult(testbase, result, '\n'.join(output_parts), skipped_reason,
                      time.monotonic() - started)


# Lock for serializing output in parallel mode
_print_lock = threading.Lock()


def main():
    args = parse_args()

    # Also accept legacy environment variables
    if args.preserve_scratch or os.environ.get('preserve_scratch') == 'yes':
        args.preserve_scratch = True
    if args.log_level == 1:
        args.log_level = int(os.environ.get('loglevel', '1'))
    if args.expect_skipped is None:
        args.expect_skipped = os.environ.get('RSYNC_EXPECT_SKIPPED', 'IGNORE')
    if args.exclude is None:
        args.exclude = os.environ.get('RSYNC_EXCLUDE', '')
    if os.environ.get('whichtests'):
        args.tests = [os.environ['whichtests']]

    # Determine directories
    tooldir = args.tooldir or os.environ.get('TOOLDIR') or os.getcwd()
    script_path = os.path.dirname(os.path.abspath(__file__))
    srcdir = args.srcdir or script_path
    if not srcdir or srcdir == '.':
        srcdir = tooldir
    rsync_bin = args.rsync_bin or os.environ.get('rsync_bin') or os.path.join(tooldir, 'rsync')
    # Absolutize: tests run with subprocess(cwd=TOOLDIR) below, so a relative
    # argv[0] would re-resolve against TOOLDIR rather than the runner's
    # invocation cwd, breaking --rsync-bin=../foo/rsync forms.  abspath()
    # captures os.getcwd() now, which is what the operator intended.
    if rsync_bin and not os.path.isabs(rsync_bin):
        rsync_bin = os.path.abspath(rsync_bin)

    # Optional second ("peer") binary for the daemon / remote-shell side, so a
    # run can mix two rsync versions. Defaults to rsync_bin -> no mixing.
    rsync_bin2 = args.rsync_bin2 or os.environ.get('rsync_bin2') or rsync_bin
    if rsync_bin2 and not os.path.isabs(rsync_bin2):
        rsync_bin2 = os.path.abspath(rsync_bin2)

    suitedir = os.path.join(srcdir, 'testsuite')
    # A backport tree excludes what its base cannot run.  Those tests never
    # run, so they must also drop out of the expected-skip set -- otherwise the
    # oracle demands a skip from a test that was never started.
    backport_excl = read_backport_exclude(tooldir, suitedir)
    if backport_excl:
        args.exclude = ','.join(x for x in (args.exclude,
                                            ','.join(sorted(backport_excl))) if x)
    if args.expect_skipped != 'IGNORE':
        args.expect_skipped = expand_skip_spec(args.expect_skipped, srcdir, suitedir)
        if backport_excl:
            args.expect_skipped = ','.join(n for n in args.expect_skipped.split(',')
                                           if n and n not in backport_excl)
    scratchbase = os.path.join(os.environ.get('scratchbase', tooldir), 'testtmp')
    os.makedirs(scratchbase, exist_ok=True)

    shconfig = read_shconfig(os.path.join(tooldir, 'shconfig'))
    tls_args = get_tls_args(os.path.join(tooldir, 'config.h'))
    setfacl_nodef = find_setfacl_nodef(scratchbase)
    rsync_cmd = build_rsync_cmd(rsync_bin, args, scratchbase)
    rsync_peer_cmd = build_rsync_cmd(rsync_bin2, args, scratchbase)

    if not os.path.isfile(rsync_bin):
        sys.stderr.write(f"rsync_bin {rsync_bin} is not a file\n")
        sys.exit(Exit.ERROR)
    if not os.path.isfile(rsync_bin2):
        sys.stderr.write(f"rsync_bin2 {rsync_bin2} is not a file\n")
        sys.exit(Exit.ERROR)
    if not os.path.isdir(srcdir):
        sys.stderr.write(f"srcdir {srcdir} is not a directory\n")
        sys.exit(Exit.ERROR)

    # Helper programs the test scripts invoke directly. Missing any of these
    # would cause many tests to fail with confusing "not found" errors, so
    # check up front and point the user at the make target that builds them.
    required_helpers = ['tls', 'trimslash', 't_unsafe', 't_chmod_secure',
                        't_secure_relpath',
                        'wildtest', 'getgroups', 'getfsdev']
    missing = [h for h in required_helpers
               if not os.path.isfile(os.path.join(tooldir, h))]
    if missing:
        sys.stderr.write(
            f"runtests.py: missing test helper program(s) in {tooldir}: "
            f"{', '.join(missing)}\n"
            f"Build them with: make {' '.join(missing)}\n"
            f"or run the full test target: make check\n"
        )
        sys.exit(Exit.ERROR)

    testuser = get_testuser()

    # Print header
    print('=' * 60)
    print(f'{sys.argv[0]} running in {tooldir}')
    print(f'    rsync_bin={rsync_cmd}')
    if rsync_peer_cmd != rsync_cmd:
        print(f'    rsync_peer={rsync_peer_cmd}')
    print(f'    srcdir={srcdir}')
    print(f'    TLS_ARGS={tls_args}')
    print(f'    testuser={testuser}')
    print(f'    os={subprocess.check_output(["uname", "-a"], text=True).strip()}')
    print(f'    preserve_scratch={"yes" if args.preserve_scratch else "no"}')
    if args.valgrind:
        print(f'    valgrind=enabled (logs in valgrind-logs/valgrind.*.log)')
    if args.parallel > 1:
        print(f'    parallel={args.parallel}')
    print(f'    daemon_transport={"tcp (loopback)" if args.use_tcp else "pipe (secure default)"}')
    print(f'    scratchbase={scratchbase}')

    # Build base environment for test scripts
    path = os.environ.get('PATH', '')
    if os.path.isdir('/usr/xpg4/bin'):
        path = '/usr/xpg4/bin:' + path

    # Make the testsuite/ directory importable so Python tests can `import rsyncfns`.
    pythonpath = suitedir
    if os.environ.get('PYTHONPATH'):
        pythonpath = suitedir + os.pathsep + os.environ['PYTHONPATH']

    base_env = os.environ.copy()
    base_env.update({
        'PATH': path,
        'POSIXLY_CORRECT': '1',
        'TOOLDIR': tooldir,
        'srcdir': srcdir,
        'RSYNC': rsync_cmd,
        'RSYNC_PEER': rsync_peer_cmd,
        'TLS_ARGS': tls_args,
        'RUNSHFLAGS': '-e',
        'scratchbase': scratchbase,
        'suitedir': suitedir,
        'TESTRUN_TIMEOUT': str(args.timeout),
        'HOME': scratchbase,
        'PYTHONPATH': pythonpath,
    })
    if args.use_tcp:
        # Opt-in: daemon tests start a real rsyncd on a claimed loopback port.
        # Default (unset) keeps the secure stdio-pipe transport.
        base_env['RSYNC_TEST_USE_TCP'] = '1'
    if args.race_timeout is not None:
        # Only exported when the operator actually passed --race-timeout: its
        # mere presence is what tells a race test to override its own default.
        base_env['race_timeout'] = str(args.race_timeout)
    else:
        # A stale value inherited from the environment would silently override
        # every test's default; the flag is the only way to set this.
        base_env.pop('race_timeout', None)
    for k, v in shconfig.items():
        if v:
            base_env[k] = v
    if setfacl_nodef:
        base_env['setfacl_nodef'] = ' '.join(setfacl_nodef)
    else:
        base_env['setfacl_nodef'] = 'true'
    if args.log_level > 8:
        base_env['RUNSHFLAGS'] = '-e -x'

    # Collect tests
    tests = collect_tests(suitedir, args.tests)
    full_run = len(args.tests) == 0

    # Drop excluded tests entirely (matched by basename against name/glob).
    excl = [e.strip() for e in args.exclude.split(',') if e.strip()]
    if excl:
        before = len(tests)
        tests = [t for t in tests
                 if not any(fnmatch.fnmatch(_testbase(t), pat) for pat in excl)]
        if before != len(tests):
            print(f"Excluding {before - len(tests)} test(s) matching: "
                  f"{', '.join(excl)}")

    # Narrow to the daemon-transport tests. The dropped count is always printed:
    # a pass that silently ran a third of the suite would read in the report as
    # if it had run all of it.
    if args.daemon_tests_only:
        tests, dropped = select_daemon_tests(tests)
        print(f"Daemon-transport tests only: running {len(tests)}, skipping "
              f"{len(dropped)} test(s) that cannot observe the transport")
        # The expected-skip list describes a full run, so it cannot be enforced
        # against a subset -- same rule as naming tests explicitly.
        full_run = False

    # An expected-result manifest defines BOTH the run set (its keys) and the
    # expected per-test outcome (its values). Used for version-mixing runs.
    expect = parse_expect_result(args.expect_result) if args.expect_result else None
    if expect is not None:
        have = {_testbase(t) for t in tests}
        unknown = sorted(k for k in expect if k not in have)
        if unknown:
            sys.stderr.write(
                "runtests.py: --expect-result lists test(s) with no matching "
                f"test file (ignored): {', '.join(unknown)}\n"
            )
        tests = [t for t in tests if _testbase(t) in expect]
        full_run = False

    def _cls(outcome):
        """Equivalence class for outcome comparison: fail and xfail both just
        mean 'broke', so a manifest 'fail' matches an actual fail OR xfail."""
        return 'broken' if outcome in ('fail', 'xfail') else outcome

    def mismatch(testbase, actual):
        """True if actual outcome disagrees with the manifest expectation."""
        return expect is not None and _cls(expect[testbase]) != _cls(actual)

    # Record test order for consistent skipped-list output
    test_order = {_testbase(t): i for i, t in enumerate(tests)}

    passed = 0
    failed = 0
    skipped = 0
    xfailed = 0
    skipped_list = []
    outcomes = {}  # testbase -> actual outcome string ('pass'/'skip'/'fail'/'xfail')
    durations = {}  # testbase -> wall-clock seconds (for --timing)

    def process_result(tr):
        """Process a TestResult and update counters. Returns True if the test
        should count as a failure for --stop-on-fail purposes."""
        nonlocal passed, failed, skipped, xfailed
        with _print_lock:
            if tr.output:
                print(tr.output)
        scratchdir = os.path.join(scratchbase, tr.testbase)
        oc = outcome_of(tr.result)
        outcomes[tr.testbase] = oc
        durations[tr.testbase] = tr.duration
        if tr.result == Exit.PASS:
            passed += 1
        elif tr.result == Exit.SKIP:
            skipped_list.append(tr.testbase)
            skipped += 1
        elif tr.result == Exit.XFAIL:
            # XFAIL: an expected failure (a known, documented residual the test
            # asserts against). Reported distinctly but does NOT fail the suite;
            # when the underlying issue is fixed the test returns 0 instead.
            xfailed += 1
        else:
            failed += 1
        if tr.result in (Exit.PASS, Exit.SKIP, Exit.XFAIL) and not args.preserve_scratch \
                and os.path.isdir(scratchdir):
            subprocess.run(['rm', '-rf', scratchdir], capture_output=True)
        # With a manifest, only a mismatch is a "failure" (an expected fail is
        # fine); without one, any non-pass/non-skip/non-xfail result is a failure.
        if expect is not None:
            return mismatch(tr.testbase, oc)
        return tr.result not in (Exit.PASS, Exit.SKIP, Exit.XFAIL)

    run_started = time.monotonic()

    if args.parallel > 1:
        # Parallel execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {}
            for testscript in tests:
                testbase = _testbase(testscript)
                scratchdir = os.path.join(scratchbase, testbase)
                timeout = 600 if ('hardlinks' in testbase or testbase == 'variety') else args.timeout
                f = executor.submit(
                    run_one_test, testscript, testbase, scratchdir,
                    base_env, timeout, srcdir, tooldir, setfacl_nodef,
                    args.always_log
                )
                futures[f] = testbase

            for f in concurrent.futures.as_completed(futures):
                tr = f.result()
                is_fail = process_result(tr)
                if is_fail and args.stop_on_fail:
                    # Cancel pending futures
                    for pending in futures:
                        pending.cancel()
                    break
    else:
        # Sequential execution
        for testscript in tests:
            testbase = _testbase(testscript)
            scratchdir = os.path.join(scratchbase, testbase)
            timeout = 600 if ('hardlinks' in testbase or testbase == 'variety') else args.timeout
            tr = run_one_test(
                testscript, testbase, scratchdir,
                base_env, timeout, srcdir, tooldir, setfacl_nodef,
                args.always_log
            )
            is_fail = process_result(tr)
            if is_fail and args.stop_on_fail:
                break

    run_wall = time.monotonic() - run_started

    # Check valgrind logs for errors
    vg_errors = 0
    if args.valgrind:
        for vlog in sorted(glob.glob(os.path.join(scratchbase, 'valgrind-logs', 'valgrind.*.log'))):
            try:
                with open(vlog) as f:
                    content = f.read()
                for line in content.splitlines():
                    if 'ERROR SUMMARY:' in line and 'ERROR SUMMARY: 0 errors' not in line:
                        vg_errors += 1
                        print(f'----- valgrind errors in {os.path.basename(vlog)}:')
                        print(content)
                        break
            except FileNotFoundError:
                pass

    # Summary
    print('-' * 60)
    print('----- overall results:')
    print(f'      {passed} passed')
    if failed > 0:
        print(f'      {failed} failed')
    if xfailed > 0:
        print(f'      {xfailed} xfailed (expected)')
    if skipped > 0:
        print(f'      {skipped} skipped')
    if vg_errors > 0:
        print(f'      {vg_errors} valgrind error(s) found (see logs in {os.path.join(scratchbase, "valgrind-logs")})')

    if args.timing:
        print_timing_report(durations, outcomes, run_wall, args.parallel)

    if expect is not None:
        # Version-mixing mode: the run is judged purely on whether each test's
        # actual outcome matched its manifest expectation. An expected 'fail'
        # is fine; an UNEXPECTED pass (xpass) or any other divergence is not.
        mismatches = []
        for tb in sorted(expect, key=lambda x: test_order.get(x, 1 << 30)):
            actual = outcomes.get(tb, 'notrun')
            if actual == 'notrun' or mismatch(tb, actual):
                mismatches.append((tb, expect[tb], actual))
        if mismatches:
            print('----- expected-result mismatches:')
            for tb, want, got in mismatches:
                tag = ' (xpass)' if _cls(want) == 'broken' and got == 'pass' else ''
                print(f'      {tb}: expected {want}, got {got}{tag}')
        print('-' * 60)
        exit_code = len(mismatches) + vg_errors
        print(f'overall result is {exit_code}')
        sys.exit(exit_code)

    skipped_str = ','.join(sorted(skipped_list, key=lambda x: test_order.get(x, 0)))
    if full_run and args.expect_skipped != 'IGNORE':
        print('----- skipped results:')
        print(f'      expected: {args.expect_skipped}')
        print(f'      got:      {skipped_str}')
    else:
        skipped_str = ''
        args.expect_skipped = ''

    print('-' * 60)

    exit_code = failed + vg_errors
    if exit_code == 0:
        # Compare the skipped set order-insensitively: which tests skipped is
        # what matters, not the order runtests happened to collect them in
        # (that order is just sorted filenames -- an easy thing to get subtly
        # wrong when maintaining the per-platform expected lists).
        got = set(s for s in skipped_str.split(',') if s)
        want = set(s for s in args.expect_skipped.split(',') if s)
        if got != want:
            exit_code = 1

    print(f'overall result is {exit_code}')
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
