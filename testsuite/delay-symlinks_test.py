#!/usr/bin/env python3
# Coverage of --delay-symlinks, alone and together with --delay-updates.
#
# The generator holds back new and changed symlinks, and generate_files()
# creates them with create_deferred_symlinks() once the receiver's redo-phase
# MSG_DONE says every file (including any --delay-updates renames) is in place,
# itemizing each one only after it has been created.
#
# The ordering checks push through a remote shell that relays the sender's data
# and stops partway through a large target file until the test lets it go on.
# The symlink sorts before its target, so by the time any of the target's data
# is sent the generator has already handled the symlink.  At the pause, the
# link therefore exists and dangles without the option, and doesn't exist yet
# with it -- no timing is involved.

import os
import shutil
import subprocess
import sys
import time

from rsyncfns import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_is_symlink, assert_same, checkit, forced_protocol, make_data_file,
    rmtree, rsh_cmd, rsync_argv, rsync_path_arg, run_rsync, test_fail,
    test_skipped,
)

proto = forced_protocol()
if proto is not None and proto < 29:
    test_skipped(f"--delay-symlinks requires protocol 29+ (negotiated {proto})")

os.environ['RSYNC_RSH'] = rsh_cmd()

TARGET_SIZE = 4 * 1024 * 1024
PAUSE_AT = 1024 * 1024          # bytes of the sender's stream to let through
LINK = os.path.join('a', 'libfoo.so')
LINK_TARGET = '../lib/libfoo.so.1'
TARGET = os.path.join('lib', 'libfoo.so.1')
OLD_LINK_TARGET = '../lib/libfoo.so.0'
OLD_TARGET = os.path.join('lib', 'libfoo.so.0')

CTL = SCRATCHDIR / 'relay-ctl'
RELAY = SCRATCHDIR / 'relay-rsh'
RELAY.write_text(f'''#!{sys.executable}
# Remote shell that runs the command locally and relays its stdin, stopping
# after DS_PAUSE_AT bytes until the file DS_CTL/resume exists.  Like ssh, it
# exits as soon as the command does.
import os, subprocess, sys, threading, time
args = sys.argv[1:]
while args and args[0].startswith('-'):
    args.pop(0)
args.pop(0)  # the host
pause_at = int(os.environ['DS_PAUSE_AT'])
ctl = os.environ['DS_CTL']
child = subprocess.Popen(['sh', '-c', ' '.join(args)], stdin=subprocess.PIPE)
# Only the command writes to the client, so it sees EOF when the command exits.
os.close(1)

def relay():
    sent = 0
    try:
        while True:
            if sent == pause_at:
                open(os.path.join(ctl, 'paused'), 'w').close()
                while not os.path.exists(os.path.join(ctl, 'resume')):
                    time.sleep(0.01)
            data = os.read(0, 65536 if sent >= pause_at else min(65536, pause_at - sent))
            if not data:
                break
            child.stdin.write(data)
            child.stdin.flush()
            sent += len(data)
        child.stdin.close()
    except (BrokenPipeError, ValueError):
        pass

threading.Thread(target=relay, daemon=True).start()
os._exit(child.wait())
''')
RELAY.chmod(0o755)


def seed_pair():
    rmtree(FROMDIR)
    rmtree(TODIR)
    (FROMDIR / 'a').mkdir(parents=True)
    (FROMDIR / 'lib').mkdir()
    make_data_file(FROMDIR / TARGET, TARGET_SIZE)
    os.symlink(LINK_TARGET, FROMDIR / LINK)


def seed_changed():
    """Like seed_pair(), but the destination already has the link pointing at
    an old target, so the transfer changes it."""
    seed_pair()
    (TODIR / 'a').mkdir(parents=True)
    (TODIR / 'lib').mkdir()
    (TODIR / OLD_TARGET).write_text("old\n")
    os.symlink(OLD_LINK_TARGET, TODIR / LINK)


def paused_push(extra, at_pause):
    """Push FROMDIR to TODIR through the relay, call at_pause() while the
    sender's data is held back partway through the target, then let the
    transfer finish.  Returns (exit code, stdout, stderr)."""
    rmtree(CTL)
    CTL.mkdir()
    env = dict(os.environ, DS_PAUSE_AT=str(PAUSE_AT), DS_CTL=str(CTL))
    argv = rsync_argv('-ai', '--no-inc-recursive', *extra,
                      f'--rsh={rsh_cmd(str(RELAY))}',
                      f'--rsync-path={rsync_path_arg()}',
                      f'{FROMDIR}/', f'lh:{TODIR}/')
    with open(CTL / 'out', 'w') as out, open(CTL / 'err', 'w') as err:
        proc = subprocess.Popen(argv, stdout=out, stderr=err, env=env)
        deadline = time.monotonic() + 60
        while not (CTL / 'paused').exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                test_fail(f"{' '.join(extra)}: the transfer never reached the "
                          f"pause point:\n{(CTL / 'err').read_text()}")
            time.sleep(0.01)
        try:
            at_pause()
        finally:
            (CTL / 'resume').touch()
        rc = proc.wait(timeout=60)
    return rc, (CTL / 'out').read_text(), (CTL / 'err').read_text()


def link_dangles():
    link = TODIR / LINK
    return os.path.islink(link) and not os.path.exists(link)


def link_target():
    link = TODIR / LINK
    return os.readlink(link) if os.path.islink(link) else None


def readonly_dirs_enforced():
    """Does a 0555 directory refuse new entries here?  It doesn't for root, or
    for a user holding CAP_DAC_OVERRIDE."""
    probe = SCRATCHDIR / 'ro-probe'
    rmtree(probe)
    probe.mkdir()
    probe.chmod(0o555)
    try:
        (probe / 'entry').touch()
    except PermissionError:
        return True
    finally:
        probe.chmod(0o755)
        rmtree(probe)
    return False


itemized_link = f'cL+++++++++ {LINK} -> {LINK_TARGET}'
can_fail_link = readonly_dirs_enforced()

# Control: without the option the symlink exists, and dangles, at the pause.
seed_pair()
seen = {}
rc, out, err = paused_push([], lambda: seen.update(dangling=link_dangles()))
if not seen['dangling']:
    test_fail("without --delay-symlinks the link should dangle at the pause "
              "point; the pause point isn't where the test expects it")
if rc != 0:
    test_fail(f"control transfer exited {rc}:\n{err}")

# Control: without the option an existing link has already been repointed at
# the new target, and dangles, at the pause.
seed_changed()
seen = {}
rc, out, err = paused_push(
    [], lambda: seen.update(target=link_target(), dangling=link_dangles()))
if seen['target'] != LINK_TARGET or not seen['dangling']:
    test_fail("without --delay-symlinks the changed link should already point "
              f"at the new target and dangle at the pause point, got {seen}")
if rc != 0:
    test_fail(f"control transfer exited {rc}:\n{err}")

for extra in ([], ['--delay-updates']):
    label = ' '.join(['--delay-symlinks', *extra])

    # With the option, nothing exists at that path until the target is in place.
    seed_pair()
    seen = {}
    rc, out, err = paused_push(
        ['--delay-symlinks', *extra],
        lambda: seen.update(link=os.path.lexists(TODIR / LINK),
                            target=os.path.exists(TODIR / TARGET)))
    if seen['target']:
        test_fail(f"{label}: the target was already in place at the pause point")
    if seen['link']:
        test_fail(f"{label}: the symlink was created before its target")
    if rc != 0:
        test_fail(f"{label}: transfer exited {rc}:\n{err}")
    assert_is_symlink(TODIR / LINK, target=LINK_TARGET, label=label)
    assert_same(TODIR / TARGET, FROMDIR / TARGET, label=label)
    if itemized_link not in out.splitlines():
        test_fail(f"{label}: the created symlink wasn't itemized:\n{out}")

    # An existing link that changes keeps pointing at its old target until the
    # new target is in place.
    seed_changed()
    seen = {}
    rc, out, err = paused_push(
        ['--delay-symlinks', *extra],
        lambda: seen.update(target=link_target(),
                            resolves=os.path.exists(TODIR / LINK)))
    if seen['target'] != OLD_LINK_TARGET or not seen['resolves']:
        test_fail(f"{label}: the changed symlink was replaced before its new "
                  f"target was in place, got {seen}")
    if rc != 0:
        test_fail(f"{label}: transfer exited {rc}:\n{err}")
    assert_is_symlink(TODIR / LINK, target=LINK_TARGET, label=label)
    lines = out.splitlines()
    if not any(line.startswith('cL') and LINK in line for line in lines):
        test_fail(f"{label}: the changed symlink wasn't itemized:\n{out}")

    # A symlink that can't be created is an error, and isn't itemized.  The
    # link's directory is made read-only at the pause point, so this is skipped
    # where a read-only directory doesn't stop the link being created.
    if can_fail_link:
        seed_pair()
        try:
            rc, out, err = paused_push(['--delay-symlinks', *extra],
                                       lambda: os.chmod(TODIR / 'a', 0o555))
        finally:
            if os.path.isdir(TODIR / 'a'):
                os.chmod(TODIR / 'a', 0o755)
        if rc != 23:
            test_fail(f"{label}: a failed symlink should exit 23, got {rc}:\n{err}")
        if any(LINK in line for line in out.splitlines()):
            test_fail(f"{label}: a symlink that failed was itemized:\n{out}")
        if LINK not in err:
            test_fail(f"{label}: the failed symlink wasn't reported:\n{err}")
        if os.path.lexists(TODIR / LINK):
            test_fail(f"{label}: the symlink exists although creating it failed")


def seed_tree():
    rmtree(FROMDIR)
    rmtree(TODIR)
    FROMDIR.mkdir(parents=True)
    make_data_file(FROMDIR / 'libfoo.so.1', 64 * 1024)
    os.symlink('libfoo.so.1', FROMDIR / 'libfoo.so')
    (FROMDIR / 'v1').mkdir()
    make_data_file(FROMDIR / 'v1' / 'data', 16 * 1024)
    os.symlink('v1', FROMDIR / 'current')


# New targets plus new symlinks to them, locally.
seed_tree()
checkit(['-ai', '--delay-symlinks', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)

# Move both symlinks to new targets and delete the old ones.
os.unlink(FROMDIR / 'libfoo.so.1')
os.unlink(FROMDIR / 'libfoo.so')
make_data_file(FROMDIR / 'libfoo.so.2', 64 * 1024)
os.symlink('libfoo.so.2', FROMDIR / 'libfoo.so')
rmtree(FROMDIR / 'v1')
(FROMDIR / 'v2').mkdir()
make_data_file(FROMDIR / 'v2' / 'data', 16 * 1024)
os.unlink(FROMDIR / 'current')
os.symlink('v2', FROMDIR / 'current')
checkit(['-ai', '--delete-delay', '--delay-symlinks', f'{FROMDIR}/', f'{TODIR}/'],
        FROMDIR, TODIR)

# Swap a regular file and a symlink for each other.
os.unlink(FROMDIR / 'libfoo.so')
(FROMDIR / 'libfoo.so').write_text("now a file\n")
os.unlink(FROMDIR / 'libfoo.so.2')
os.symlink('libfoo.so', FROMDIR / 'libfoo.so.2')
checkit(['-ai', '--delete', '--delay-symlinks', f'{FROMDIR}/', f'{TODIR}/'],
        FROMDIR, TODIR)

# Replace a non-empty directory with a symlink: --force deletes the directory
# tree when the deferred link is created, and those deletions are counted the
# same as without the option.
os.unlink(FROMDIR / 'current')
os.symlink('v2', FROMDIR / 'current')
os.unlink(TODIR / 'current')
(TODIR / 'current' / 'sub').mkdir(parents=True)
(TODIR / 'current' / 'file').write_text("in the way\n")
(TODIR / 'current' / 'sub' / 'file').write_text("in the way\n")
plain = SCRATCHDIR / 'to-plain'
rmtree(plain)
shutil.copytree(TODIR, plain, symlinks=True)


def deletion_stats(*args):
    proc = run_rsync('-a', '--force', '--stats', *args, capture_output=True)
    return [line for line in proc.stdout.splitlines()
            if line.startswith('Number of deleted files')]


expected = deletion_stats(f'{FROMDIR}/', f'{plain}/')
got = deletion_stats('--delay-symlinks', f'{FROMDIR}/', f'{TODIR}/')
if got != expected:
    test_fail(f"--delay-symlinks reported {got} deletions, "
              f"{expected} without it")
rmtree(plain)
checkit(['-ai', '--force', '--delay-symlinks', f'{FROMDIR}/', f'{TODIR}/'],
        FROMDIR, TODIR)

# An unchanged symlink is left alone and not itemized.
ino = os.lstat(TODIR / 'current').st_ino
proc = run_rsync('-ai', '--delay-symlinks', f'{FROMDIR}/', f'{TODIR}/',
                 capture_output=True)
if os.lstat(TODIR / 'current').st_ino != ino:
    test_fail("an unchanged symlink was recreated")
if 'current' in proc.stdout:
    test_fail(f"an unchanged symlink was itemized:\n{proc.stdout}")

# A symlink inside a read-only directory: rsync makes the directory writable
# during the transfer and restores its mode at the end, after the symlinks.
seed_tree()
(FROMDIR / 'ro').mkdir()
os.symlink('../libfoo.so.1', FROMDIR / 'ro' / 'link')
os.chmod(FROMDIR / 'ro', 0o555)
try:
    checkit(['-ai', '--delay-symlinks', f'{FROMDIR}/', f'{TODIR}/'],
            FROMDIR, TODIR)
finally:
    os.chmod(FROMDIR / 'ro', 0o755)
    if os.path.isdir(TODIR / 'ro'):
        os.chmod(TODIR / 'ro', 0o755)

# --dry-run creates nothing.
seed_tree()
run_rsync('-a', '--delay-symlinks', '--dry-run', f'{FROMDIR}/', f'{TODIR}/')
if os.path.lexists(TODIR):
    test_fail("--dry-run created the destination")
