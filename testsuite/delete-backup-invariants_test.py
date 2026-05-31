#!/usr/bin/env python3
"""Workstream-1 invariant group D -- deletion and backup.

Derived from rsync.1.md + generator.c delete_in_dir, delete.c, backup.c.

  D1:  --delete only removes within recursed directories. A sibling dir that
       is NOT recursed into is left untouched. We assert the deletion bound:
       an extra file inside the recursed tree is removed; an extra inside a
       dir the transfer never descends into survives.

  D2:  (prime transport-equivalence case) a sender-side IO error SUPPRESSES
       all deletion unless --ignore-errors. generator.c delete_in_dir ~297:
       `if (io_error & IOERR_GENERAL && !ignore_errors) ... skip deletion`.
       io_error is WIRE-PROPAGATED, so this must behave identically local vs
       daemon. Mechanism: an UNREADABLE SOURCE DIRECTORY (mode 000) makes the
       sender's opendir() fail during the flist scan -- which runs BEFORE
       deletion -- setting io_error. (An unreadable regular file fails only
       later in send_files, AFTER --delete-during has already deleted, so it
       does NOT reliably suppress; the unreadable-directory mechanism is the
       one that sets io_error pre-deletion.) We use --delete-before so the
       suppression is DETERMINISTIC: with --delete-during/-delay the top-
       level dir is deleted before the scan even reaches the unreadable
       subdir that sets io_error, so a shallow extra races the error;
       --delete-before (and -after) finish the io_error-setting scan before
       any deletion, which is the regime that actually exercises the
       delete_in_dir guard (verified on HEAD: -before/-after suppress,
       -during/-delay race). We assert:
         * --delete-before alone           -> NO deletions (extra survives)
         * --delete-before --ignore-errors -> deletions proceed (extra gone)
       and that both behaviors are IDENTICAL across local and daemon legs.

  max-delete: --max-delete=N deletes at most N files (delete.c ~156). We
       assert the cap holds: with N extras > limit, exactly limit are removed.

  D4:  delete timing default is protocol/version dependent
       (--delete-during/-before/-after). We assert the FINAL deletion SET is
       identical across every explicit timing variant, NEVER mid-transfer
       ordering.

  backup: --backup renames the existing dst before overwrite (backup.c
       make_backup). We assert the backup file (with ~ suffix, and with
       --backup-dir) holds the OLD content while the dst holds the NEW
       content.
"""

import os
import subprocess

import rsyncfns
from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, run_rsync, test_fail,
)
from equiv_fns import (
    SkipLeg, _bin_argv, _join_slash, _start_daemon_for_bin, write_daemon_conf,
)

ROOT = SCRATCHDIR / 'dgroup'
rmtree(ROOT)
makepath(ROOT)


def read_file(p):
    return p.read_text()


# --------------------------------------------------------------------------
# D1 -- --delete only removes within recursed directories.
# --------------------------------------------------------------------------
# Transfer src/keep/ (a single subdir) into dst/keep/. The dst ALSO has a
# sibling dst/untouched/ that the transfer never names. --delete may remove
# the extra inside dst/keep/ but MUST NOT touch dst/untouched/.
d1 = ROOT / 'd1'
src = d1 / 'src'
dst = d1 / 'dst'
makepath(src / 'keep', dst / 'keep', dst / 'untouched')
(src / 'keep' / 'f.txt').write_text('kept content\n')
(dst / 'keep' / 'extra_in_recursed.txt').write_text('should be deleted\n')
(dst / 'untouched' / 'extra_in_sibling.txt').write_text('must survive\n')

# Transfer ONLY the keep subtree (src/keep/ -> dst/keep/). dst/untouched is
# outside the transfer's namespace and is never recursed into.
run_rsync('-a', '--delete', f'{src}/keep/', f'{dst}/keep/')
if (dst / 'keep' / 'extra_in_recursed.txt').exists():
    test_fail('D1 VIOLATION: extra file inside the recursed dir was NOT '
              'deleted; --delete should remove it.')
if not (dst / 'untouched' / 'extra_in_sibling.txt').exists():
    test_fail('D1 VIOLATION: a file in a sibling dir the transfer never '
              'recursed into was deleted. --delete must be bounded to the '
              'recursed tree.')


# --------------------------------------------------------------------------
# D2 -- sender IO error suppresses deletion (transport-equivalent).
# --------------------------------------------------------------------------
# Mechanism (see module docstring): an unreadable SOURCE DIRECTORY makes the
# sender's opendir fail during the flist scan -> io_error set BEFORE deletion.
def d2_setup(work, *, ignore_errors):
    """Populate a work dir for a D2 leg.

    Layout (work is the daemon module root):
        work/src/good/a.txt        (transfers fine)
        work/src/blocked/          (mode 000 -> opendir fails on sender)
        work/dst/good/a.txt
        work/dst/extra_to_delete.txt  (the deletion probe)
    Returns the dst path.
    """
    rmtree(work)
    src = work / 'src'
    dst = work / 'dst'
    makepath(src / 'good', src / 'blocked', dst / 'good')
    (src / 'good' / 'a.txt').write_text('transferable body\n')
    (src / 'blocked' / 'inner.txt').write_text('cannot be read\n')
    (dst / 'extra_to_delete.txt').write_text('deletion probe\n')
    os.chmod(src / 'blocked', 0o000)
    return dst


def run_d2_leg(transport, *, ignore_errors, port):
    """Run the D2 scenario over one transport. Returns whether the extra
    deletion-probe file SURVIVED (True == deletion suppressed).

    Each daemon leg gets a DISTINCT port: the test framework leaves prior
    test daemons alive until process exit (atexit kill), so reusing a port
    would let a stale daemon -- still serving the previous leg's module path
    -- answer the connection and silently produce the wrong result.
    """
    rsync_bin = rsyncfns.RSYNC
    work = ROOT / f'd2-{transport}-{"ign" if ignore_errors else "plain"}'
    dst = d2_setup(work, ignore_errors=ignore_errors)
    src = _join_slash(work, 'src/')
    opts = ['-a', '--delete-before']
    if ignore_errors:
        opts.append('--ignore-errors')
    base = _bin_argv(rsync_bin) + opts

    try:
        if transport == 'local':
            argv = base + [src, f'{dst}/']
        elif transport in ('daemon_pipe', 'daemon_tcp'):
            use_tcp = (transport == 'daemon_tcp')
            if use_tcp and not rsyncfns.USE_TCP:
                raise SkipLeg('daemon_tcp needs --use-tcp')
            tag = f'd2-{transport}-{"ign" if ignore_errors else "plain"}'
            conf = write_daemon_conf(
                [('equiv', {'path': str(work), 'read only': 'no'})],
                globals={'pid file': str(work / 'rsyncd.pid')},
                name=f'{tag}.conf',
            )
            if use_tcp:
                # A prior daemon_pipe leg sets RSYNC_CONNECT_PROG, which would
                # hijack this TCP client into the stale pipe daemon. Clear it.
                os.environ.pop('RSYNC_CONNECT_PROG', None)
            prefix = _start_daemon_for_bin(rsync_bin, conf, port, use_tcp=use_tcp)
            argv = base + [src, f'{prefix}equiv/dst/']
        else:
            raise ValueError(transport)
        # Restore perms after the run regardless of outcome so cleanup works.
        proc = subprocess.run(argv, capture_output=True, text=True)
        # A sender IO error yields exit 23 (partial); that is expected here.
        if proc.returncode not in (0, 23):
            test_fail(f'[D2/{transport}] unexpected exit {proc.returncode}: '
                      f'{" ".join(argv)}\n{proc.stderr}')
        survived = (dst / 'extra_to_delete.txt').exists()
        return survived
    finally:
        # Restore perms on every dir under the work tree so the scratch dir
        # can be cleaned (the transfer may have created a mode-000 'blocked'
        # dir in the dst). Walk bottom-up, chmod 0o755 any unreadable dir.
        for dirpath, dirnames, _ in os.walk(work):
            for d in dirnames:
                p = os.path.join(dirpath, d)
                try:
                    os.chmod(p, 0o755)
                except OSError:
                    pass


# Run both modes across local + daemon_pipe (+ daemon_tcp under --use-tcp).
d2_transports = ['local', 'daemon_pipe']
if rsyncfns.USE_TCP:
    d2_transports.append('daemon_tcp')

# D2 relies on chmoding a source dir to 0o000 to force an opendir IO-error.
# Under euid==0 (real root or fakeroot), DAC is bypassed: the mode-000 dir is
# still readable, the IO-error never fires, and the "suppressed" leg falsely
# looks like a VIOLATION. Skip D2 cleanly (print a notice and leave
# suppress_results/ignore_results empty so the cross-transport checks below
# are also skipped) rather than false-failing under fakeroot/root. The rest
# of the test (max-delete, D4, backup, D7) continues unaffected.
_d2_root_skip = (os.geteuid() == 0)
if _d2_root_skip:
    print('D2 SKIPPED: euid==0 -- DAC bypass means mode-000 dir trick cannot '
          'force an opendir IO-error under root; D2 is not verifiable here.')

suppress_results = {}
ignore_results = {}
_next_port = [12893]
def _port():
    p = _next_port[0]
    _next_port[0] += 1
    return p
if not _d2_root_skip:
    for t in d2_transports:
        try:
            suppress_results[t] = run_d2_leg(t, ignore_errors=False, port=_port())
            ignore_results[t] = run_d2_leg(t, ignore_errors=True, port=_port())
        except SkipLeg as e:
            print(f'[D2/{t}] skipped: {e}')

    if not suppress_results:
        test_fail('D2: no transport legs ran')

# Per-leg invariant: --delete alone suppresses (survived), --ignore-errors
# proceeds (deleted).
for t in suppress_results:
    if not suppress_results[t]:
        test_fail(f'[D2/{t}] VIOLATION: a sender IO error did NOT suppress '
                  f'deletion. The deletion probe was removed under --delete '
                  f'alone; delete_in_dir must skip deletion when io_error is '
                  f'set (this protects against destructive deletion on a '
                  f'broken/partial transfer).')
    if ignore_results[t]:
        test_fail(f'[D2/{t}] VIOLATION: --ignore-errors did NOT re-enable '
                  f'deletion; the deletion probe survived. With '
                  f'--ignore-errors, io_error must not suppress deletion.')

# Transport-equivalence: every leg must agree on BOTH behaviors. io_error is
# wire-propagated, so a daemon leg that disagreed with local would be a
# silent transport divergence. (Skipped under root; suppress_results is empty.)
if not _d2_root_skip:
    if len(set(suppress_results.values())) != 1:
        test_fail(f'D2 TRANSPORT DIVERGENCE: IO-error deletion suppression '
                  f'differs across transports: {suppress_results}')
    if len(set(ignore_results.values())) != 1:
        test_fail(f'D2 TRANSPORT DIVERGENCE: --ignore-errors deletion behavior '
                  f'differs across transports: {ignore_results}')


# --------------------------------------------------------------------------
# max-delete -- at most N files removed.
# --------------------------------------------------------------------------
md = ROOT / 'maxdelete'
src = md / 'src'
dst = md / 'dst'
makepath(src, dst)
(src / 'keep.txt').write_text('keep\n')
N_EXTRA = 5
LIMIT = 2
for i in range(N_EXTRA):
    (dst / f'extra{i}.txt').write_text('x\n')
# rsync exits 25 when the max-delete limit stops further deletions.
run_rsync('-a', '--delete', f'--max-delete={LIMIT}', f'{src}/', f'{dst}/',
          check=False)
remaining = sorted(dst.glob('extra*'))
if len(remaining) != N_EXTRA - LIMIT:
    test_fail(f'max-delete VIOLATION: with --max-delete={LIMIT} and '
              f'{N_EXTRA} extras, expected {N_EXTRA - LIMIT} survivors, '
              f'found {len(remaining)}: {remaining}. The cap must bound '
              f'deletions at N.')


# --------------------------------------------------------------------------
# D4 -- delete-timing variants yield the SAME final deletion set.
# --------------------------------------------------------------------------
# Assert FINAL STATE only, never mid-transfer ordering.
d4src = ROOT / 'd4src'
makepath(d4src / 'sub')
(d4src / 'a.txt').write_text('a\n')
(d4src / 'sub' / 'b.txt').write_text('b\n')

EXTRAS = ['extra_top.txt', 'sub/extra_deep.txt']
final_sets = {}
for variant in ('--delete-before', '--delete-during', '--delete-delay',
                '--delete-after'):
    d4dst = ROOT / f'd4dst-{variant.strip("-")}'
    rmtree(d4dst)
    makepath(d4dst / 'sub')
    for e in EXTRAS:
        (d4dst / e).write_text('garbage\n')
    run_rsync('-a', '--delete', variant, f'{d4src}/', f'{d4dst}/')
    # Record the surviving extras (the complement of the deletion set).
    survivors = {e for e in EXTRAS if (d4dst / e).exists()}
    final_sets[variant] = survivors

distinct = {frozenset(s) for s in final_sets.values()}
if len(distinct) != 1:
    test_fail(f'D4 VIOLATION: delete-timing variants produced DIFFERENT '
              f'final deletion sets: {final_sets}. The timing must not '
              f'change the final state, only when deletion happens.')
if distinct != {frozenset()}:
    test_fail(f'D4 VIOLATION: some extras were NOT deleted: {final_sets}')


# --------------------------------------------------------------------------
# backup -- old content preserved in backup, new content in dst.
# --------------------------------------------------------------------------
# Plain --backup (~ suffix).
bk = ROOT / 'backup'
src = bk / 'src'
dst = bk / 'dst'
makepath(src, dst)
(src / 'f.txt').write_text('NEW content -- distinct length\n')
(dst / 'f.txt').write_text('OLD content\n')
# Give the source a clearly newer mtime so the quick-check (size+mtime) sees
# a change and re-sends; the existing dst is then backed up before overwrite.
os.utime(src / 'f.txt', (10_000_000_000, 10_000_000_000))
run_rsync('-a', '--backup', f'{src}/', f'{dst}/')
if read_file(dst / 'f.txt') != 'NEW content -- distinct length\n':
    test_fail('backup VIOLATION: dst does not hold the NEW content after '
              '--backup overwrite.')
bak = dst / 'f.txt~'
if not bak.exists():
    test_fail('backup VIOLATION: expected backup file f.txt~ was not created.')
if read_file(bak) != 'OLD content\n':
    test_fail(f'backup VIOLATION: backup file does not hold the OLD content; '
              f'got {read_file(bak)!r}. make_backup must rename the existing '
              f'dst before overwrite.')

# --backup-dir: backup lands under the backup dir, holding the OLD content.
bkd = ROOT / 'backupdir'
src = bkd / 'src'
dst = bkd / 'dst'
bdir = bkd / 'bak'
makepath(src, dst, bdir)
(src / 'f.txt').write_text('NEW2 content -- distinct length\n')
(dst / 'f.txt').write_text('OLD2 content\n')
os.utime(src / 'f.txt', (10_000_000_000, 10_000_000_000))
run_rsync('-a', '--backup', f'--backup-dir={bdir}', f'{src}/', f'{dst}/')
if read_file(dst / 'f.txt') != 'NEW2 content -- distinct length\n':
    test_fail('backup-dir VIOLATION: dst does not hold the NEW content.')
bdfile = bdir / 'f.txt'
if not bdfile.exists():
    test_fail('backup-dir VIOLATION: backup not placed in --backup-dir.')
if read_file(bdfile) != 'OLD2 content\n':
    test_fail(f'backup-dir VIOLATION: backup-dir copy does not hold the OLD '
              f'content; got {read_file(bdfile)!r}.')

# --------------------------------------------------------------------------
# D7 -- daemon backup-dir confinement (TCP-daemon only).
# --------------------------------------------------------------------------
# A daemon sanitizes --backup-dir: a leading slash is replaced by the module
# path, so an absolute --backup-dir=/escape is rooted INSIDE the module
# (rsyncd.conf.5.md ~241). The client cannot make the daemon write backups
# outside the module via an absolute path. We push to a module with --backup
# --backup-dir=/escape and assert the backup landed at <module>/escape, NOT
# at the real filesystem /escape. Needs a bound socket; cleanly skips when
# --use-tcp is not set.
_d2_status = ('D2 skipped (root/fakeroot)' if _d2_root_skip
              else f'D2 legs: suppress={suppress_results}, ignore={ignore_results}')
if not rsyncfns.USE_TCP:
    print('delete-backup-invariants: D7 (daemon backup-dir confinement) '
          f'skipped (needs --use-tcp). D1/max-delete/D4/backup verified. '
          f'{_d2_status}')
else:
    d7 = ROOT / 'd7'
    module_root = d7 / 'module'
    src = d7 / 'src'
    rmtree(d7)
    makepath(module_root, src)
    # Seed the module with an existing file that will be overwritten (and so
    # backed up) by the push.
    (module_root / 'f.txt').write_text('OLD daemon content\n')
    (src / 'f.txt').write_text('NEW daemon content -- distinct length\n')
    os.utime(src / 'f.txt', (10_000_000_000, 10_000_000_000))

    conf = write_daemon_conf(
        [('bk', {'path': str(module_root), 'read only': 'no'})],
        globals={'pid file': str(d7 / 'rsyncd.pid')},
        name='d7-backupconf.conf',
    )
    os.environ.pop('RSYNC_CONNECT_PROG', None)
    prefix = _start_daemon_for_bin(rsyncfns.RSYNC, conf, 12899, use_tcp=True)

    # Sentinel: ensure /escape (real fs root) is not writable garbage we'd
    # mistake for success; we never expect anything to be created there.
    argv = _bin_argv(rsyncfns.RSYNC) + [
        '-a', '--backup', '--backup-dir=/escape',
        f'{src}/', f'{prefix}bk/',
    ]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode not in (0, 23):
        test_fail(f'[D7] push exited {proc.returncode}: {" ".join(argv)}\n'
                  f'{proc.stderr}')

    # The new content must be in the module.
    if read_file(module_root / 'f.txt') != 'NEW daemon content -- distinct length\n':
        test_fail('D7: module dst does not hold the NEW content.')
    # The backup of the OLD content must be CONFINED to the module: the
    # leading-slash path /escape is rooted at <module>/escape.
    confined = module_root / 'escape' / 'f.txt'
    if not confined.exists():
        test_fail('D7 VIOLATION: --backup-dir=/escape backup was not found at '
                  f'<module>/escape ({confined}). The daemon must sanitize a '
                  'leading-slash backup-dir to be module-rooted.')
    if read_file(confined) != 'OLD daemon content\n':
        test_fail(f'D7 VIOLATION: confined backup does not hold the OLD '
                  f'content; got {read_file(confined)!r}.')
    print('delete-backup-invariants: D1/max-delete/D4/backup + D7 (daemon '
          f'backup-dir confinement, TCP) verified. {_d2_status}')
