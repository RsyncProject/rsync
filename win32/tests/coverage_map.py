#!/usr/bin/env python3
"""Map upstream's testsuite onto this port's tests, and name what is missing.

Upstream ships ~350 tests, one file per narrow regression.  This port ships two
dozen, each covering a topic with many assertions.  Comparing the two by count
says nothing, and a hand-written mapping table would be stale one rebase later.
So the mapping is computed here instead, and the answer is regenerated whenever
it is asked for.

Every upstream test lands in exactly one bucket:

  * a *reason* it cannot apply to this port -- daemon mode, symlink creation,
    ACLs/xattrs, Unix ids and device files, rrsync, TCP/ASan-only probes.  Each
    reason is a documented non-goal (see BUILD-CMAKE.md), not an oversight.
  * the *port test* that covers the same ground, via COVERED_BY below.
  * otherwise it is an uncovered gap, listed explicitly.

Run it two ways:

    python win32/tests/coverage_map.py            # human summary + gap list
    python win32/tests/coverage_map.py --markdown # the table README.md carries
    python win32/tests/coverage_map.py --check    # exit 1 on an unreviewed gap

`--check` is the point of the whole file.  A rebase onto a new upstream release
brings new tests with it; any that is applicable to Windows and neither covered
nor listed in KNOWN_GAPS fails the check, so it has to be looked at rather than
silently ignored.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import argparse
import pathlib
import re
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
SUITE = ROOT / 'testsuite'
PORT_TESTS = ROOT / 'win32' / 'tests'

# --- why a test cannot apply here ------------------------------------------
# Ordered: the first match wins, so the most decisive reason is listed first.
# Each entry is (bucket, name regex, body regex, one-line reason).
INAPPLICABLE = [
    ('tcp', None, r'require_tcp\(',
     'needs the real-TCP transport; upstream skips these under the default '
     '`make check` too (testsuite/skiplist/common.txt)'),
    ('asan', None, r'require_asan\(',
     'needs an AddressSanitizer build'),
    ('daemon', r'^(daemon|chroot|inband-modname|early-input|stdio)', None,
     'daemon mode is not supported on Windows'),
    ('rrsync', r'^rrsync', None,
     'rrsync is a Perl/POSIX wrapper script, not part of the port'),
    ('ssl', r'^(rsync-ssl|proxy)', None,
     'rsync-ssl needs stunnel/openssl and a daemon to talk to'),
    ('acl_xattr', r'(^acls?\b|acl|xattr)', None,
     'ACLs and xattrs are not supported on Windows'),
    ('symlink', r'symlink|safe-links|unsafe|keep-dirlinks|no-implied-dirs'
                r'|dest-symlinked', None,
     'the port reports links rather than creating them: making one needs a '
     'privilege most accounts lack'),
    ('ids', r'^(chown|devices|fake-super|nonroot|uidlist|macos|chmod-setid)',
     None, 'Unix uid/gid ownership, device nodes and fake-super have no '
           'Windows equivalent'),
    ('daemon', None, r'stdio_daemon|start_daemon|rsyncd\.conf|--daemon\b|\bchroot\b',
     'daemon mode is not supported on Windows'),
    ('symlink', None, r'os\.symlink|(?<![a-z_])symlink\(',
     'the test builds a symlink, which this port cannot create'),
    ('acl_xattr', None, r'setfacl|getfattr|\bxattr',
     'ACLs and xattrs are not supported on Windows'),
    ('ids', None, r'os\.mkfifo|mknod|--devices\b|--specials\b|(?<![a-z_])chown\(',
     'Unix ownership and special files have no Windows equivalent'),
]

# --- which port test covers which upstream subject -------------------------
# Matched in order against the upstream test name.  These are topic mappings,
# not one-to-one claims: the port test covers the same behaviour, usually with
# fewer files and more assertions per file.
COVERED_BY = [
    (r'^(00-hello|hands|dirs|longdir|missing|executability|atimes|crtimes'
     r'|open-noatime|stop-time|simd-checksum|preallocate)', 'basics'),
    (r'^(alt-dest|link-dest|operator-path-(link|copy|compare)-dest)', 'altdest'),
    (r'^(backup|operator-path-backup)', 'backup'),
    (r'^(batch-mode|write-batch|batch-only|operator-path-write-batch'
     r'|scanner-batch)', 'batch'),
    (r'^(compress|compare)', 'compress'),
    (r'^(delete|malicious-.*delete|peer-legacy-implied-delete'
     r'|scanner-delete-delay)', 'delete_exclude'),
    (r'^(delta|hashsearch|append|change-shrink|change-vanish|growing-file'
     r'|source-change-size|match-)', 'delta'),
    (r'^(filenames|log-control-chars|ki58-log-format)', 'filenames'),
    (r'^(files-from|delete-missing-args-files-from'
     r'|operator-path-files-from)', 'files_from'),
    (r'^(filter|merge|cvs-exclude|exclude|size-filter|prune-empty-dirs'
     r'|ki73-cvs)', 'filters'),
    (r'^fuzzy', 'fuzzy'),
    (r'^(clean-fname|hashtable-overflow|iwildmatch|trimslash|wildmatch'
     r'|secure-relpath|safe-arg|skiplist-spec|max-alloc-zero'
     r'|recv-discard-nullderef|scanner-argv-bounds)', 'helpers'),
    (r'^(inplace|partial|readonly-partial)', 'inplace_append'),
    (r'^(output-options|metadata-depth|ki62-io-error-mask)', 'itemize'),
    (r'^(hardlinks|link-dest-module-escape|link-dest-pathroot)',
     'links_reported'),
    (r'^(local-copy|reverse-daemon-delta)', 'local_copy'),
    (r'^(chmod|mkpath|file-to-file-mkpath|sparse|msg-io-timeout'
     r'|authenticate-no-ocloexec|git-set-file-times|highfd-hang'
     r'|connect-prog|remote-shell-newline)', 'options'),
    (r'^(longdir|deep-path|operator-path-(log-file|insecure-links))', 'paths'),
    (r'^(relative|dirs)', 'relative'),
    (r'^(ssh-basic|sender-remove-source)', 'remote'),
    (r'^(temp-dir|delay-updates|operator-path-(temp-dir|partial-dir|inplace)'
     r'|partial-dir-abs-delta|partial_nowrite)', 'transfer_control'),
]

# Applicable upstream tests with no port equivalent, each with the reason it is
# acceptable to leave uncovered.  Anything applicable that is NOT here and NOT
# matched by COVERED_BY fails --check, which is how a rebase surfaces new work.
KNOWN_GAPS = {
    'operator-path-dir-daemon-inmodule':
        'named operator-path, but the case it tests is a daemon module; there '
        'is no module to be inside without daemon mode',
    'scanner-daemon-log-checksum':
        'daemon-scoped despite the generic name -- it drives the daemon log',
}


def upstream_tests():
    return sorted(p.name[:-len('_test.py')] for p in SUITE.glob('*_test.py'))


def port_tests():
    return sorted(p.name[len('test_'):-3] for p in PORT_TESTS.glob('test_*.py'))


def classify(name):
    """Return (bucket, reason) -- bucket 'applicable' means it should run here."""
    src = (SUITE / f'{name}_test.py').read_text(encoding='utf-8', errors='replace')
    for bucket, name_re, body_re, reason in INAPPLICABLE:
        if name_re and re.search(name_re, name, re.I):
            return bucket, reason
        if body_re and re.search(body_re, src, re.I):
            return bucket, reason
    return 'applicable', None


def covered_by(name):
    for pat, port_test in COVERED_BY:
        if re.search(pat, name):
            return port_test
    return None


def analyse():
    buckets, reasons = {}, {}
    covered = defaultdict(list)
    gaps = []
    for name in upstream_tests():
        bucket, reason = classify(name)
        buckets[name] = bucket
        if reason:
            reasons.setdefault(bucket, reason)
            continue
        port_test = covered_by(name)
        if port_test:
            covered[port_test].append(name)
        else:
            gaps.append(name)
    return buckets, reasons, covered, gaps


BUCKET_TITLES = {
    'applicable': 'applicable to this port',
    'daemon': 'daemon / chroot mode',
    'symlink': 'symlink creation',
    'acl_xattr': 'ACLs and xattrs',
    'ids': 'Unix ids, devices, fake-super',
    'rrsync': 'rrsync wrapper',
    'ssl': 'rsync-ssl / proxy',
    'tcp': 'real-TCP transport',
    'asan': 'AddressSanitizer build',
}


def render_markdown(buckets, reasons, covered, gaps):
    total = len(buckets)
    counts = Counter(buckets.values())
    applicable = counts['applicable']
    out = []
    out.append('| upstream tests | count | status |')
    out.append('| --- | ---: | --- |')
    out.append(f'| **total in `testsuite/`** | {total} | |')
    for bucket, _ in Counter(
            {k: v for k, v in counts.items() if k != 'applicable'}).most_common():
        out.append(f'| {BUCKET_TITLES.get(bucket, bucket)} | {counts[bucket]} '
                   f'| not applicable -- {reasons[bucket]} |')
    ncov = sum(len(v) for v in covered.values())
    out.append(f'| **applicable here** | {applicable} | '
               f'{ncov} covered, {len(gaps)} not |')
    out.append('')
    out.append('| port test | upstream tests it covers |')
    out.append('| --- | --- |')
    for port_test in port_tests():
        names = covered.get(port_test, [])
        if names:
            out.append(f'| `{port_test}` | {len(names)}: '
                       + ', '.join(f'`{n}`' for n in sorted(names)[:6])
                       + (f' and {len(names) - 6} more' if len(names) > 6 else '')
                       + ' |')
        else:
            out.append(f'| `{port_test}` | Windows-specific; no upstream equivalent |')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--markdown', action='store_true',
                    help='emit the table README.md carries')
    ap.add_argument('--check', action='store_true',
                    help='exit 1 if an applicable upstream test is unreviewed')
    args = ap.parse_args()

    if not SUITE.is_dir():
        print(f'no testsuite/ at {SUITE}', file=sys.stderr)
        return 2

    buckets, reasons, covered, gaps = analyse()
    unreviewed = [g for g in gaps if g not in KNOWN_GAPS]

    if args.markdown:
        print(render_markdown(buckets, reasons, covered, gaps))
        return 0

    counts = Counter(buckets.values())
    print(f'upstream tests: {len(buckets)}    port tests: {len(port_tests())}')
    for bucket, n in counts.most_common():
        print(f'  {BUCKET_TITLES.get(bucket, bucket):32} {n:4}')
    print(f'\ncovered by a port test: {sum(len(v) for v in covered.values())}')
    print(f'accepted gaps         : {len(gaps) - len(unreviewed)}')
    print(f'unreviewed            : {len(unreviewed)}')
    for g in unreviewed:
        print(f'    {g}')

    if args.check and unreviewed:
        print('\nAn upstream test applies to this port and is neither covered '
              'nor listed in KNOWN_GAPS.\nAdd it to a win32/tests test, or to '
              'KNOWN_GAPS with the reason it can be left.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
