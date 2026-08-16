#!/usr/bin/env python3
# Finding [26]: support/rrsync leaves -D / --specials enabled in a restricted
# (non-"/") directory, so a restricted SSH user can have the spawned rsync
# create special files (FIFOs, sockets; device nodes too if the receiver is
# privileged) inside the served tree -- objects the forced-command policy did
# not intend to permit, and that the user cannot otherwise create through the
# wrapper (it only runs rsync, not a shell).
#
# Part 1 shows --specials is the toggle that enables special-file creation (a
# FIFO is created with it, skipped without it).  Part 2 is the fix: a restricted
# rrsync no longer creates special files even when -D/--specials is requested.
#
# Rejecting -D outright broke plain `rsync -a` (which bundles -rlptgoD) for every
# restricted rrsync (see rrsync-archive-mode), so rather than reject the transfer
# the wrapper forces --drop-D, which refuses the creation (entries are skipped,
# exactly the part-1 "without --specials" behaviour) while the rest of the
# transfer proceeds.  Runs at any uid.
#
# It is --drop-D and not --no-D because --no-D also clears the rdev fields that
# devices and special files carry on the wire, and the wrapper sets it on one
# end of the connection only: the client's -D sender then writes rdev the
# receiver never reads and the file list desynchronises.  --drop-D withholds
# only the creation.
#
# The two directions are asserted separately, because they are not the same
# question.  Creation happens where files are WRITTEN, so only the receiving
# side can create anything in the served tree, and only there is there anything
# to deny.  Part 4 pins that down so the distinction cannot quietly collapse
# back into "forced everywhere".

import os
import shlex
import subprocess

from rsyncfns import (
    RSYNC, RSYNC_PREFIX, SCRATCHDIR, patched_rrsync, rmtree, rsync_argv,
    test_fail, xattr_set, xattrs_supported, rsync_path_arg,
)

base = SCRATCHDIR / 'rrsync-specials'
rmtree(base)
src = base / 'src'
src.mkdir(parents=True)
os.mkfifo(src / 'evil_fifo')
(src / 'ordinary').write_text('PLAIN\n')

# --- Part 1: --specials enables special-file creation in the destination.
withd = base / 'with'
withd.mkdir()
subprocess.run(rsync_argv('-rlptgo', '--specials', f'{src}/', f'{withd}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if not (withd / 'evil_fifo').is_fifo():
    test_fail("premise check failed: --specials should create the FIFO in the dest")
without = base / 'without'
without.mkdir()
subprocess.run(rsync_argv('-rlptgo', f'{src}/', f'{without}/'),
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if (without / 'evil_fifo').exists():
    test_fail("premise check failed: without --specials the FIFO should be skipped")

# --- Part 2: a restricted rrsync accepts -D/--specials but forces --drop-D, so
# the special-file creation shown in part 1 cannot be requested through the
# wrapper.  Use an argv-recording stub rsync to confirm that the option is
# accepted (the `rsync -a` regression is gone), that --drop-D is forced, and
# that the client's own -D still reaches the server so both ends keep framing
# the file list the same way.
record = base / 'argv'
stub = base / 'rsync-record'
stub.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" >' + str(record) + '\n')
stub.chmod(0o755)
rrsync = patched_rrsync(base, rsync_path=str(stub))
restricted = base / 'restricted'
restricted.mkdir(exist_ok=True)

def forwarded_opts(cmd):
    if record.exists():
        record.unlink()
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': cmd}
    r = subprocess.run([str(rrsync), str(restricted)], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        test_fail(f"restricted rrsync rejected `{cmd}` instead of stripping "
                  f"the option (exit {r.returncode}): {r.stderr.strip()}")
    return record.read_text().split('\n') if record.exists() else []


for opt in ('-D', '--specials'):
    # Receiving side: this is the one that could create something here.
    forwarded = forwarded_opts(f'rsync --server {opt} . .')
    if '--drop-D' not in forwarded:
        test_fail(f"restricted rrsync did not force --drop-D for `{opt}` on "
                  f"the receiving side, where creation happens: {forwarded}")
    # ...and the client's own request must still reach the server, or the two
    # ends frame the file list's rdev fields differently and it desynchronises.
    if opt not in forwarded:
        test_fail(f"restricted rrsync dropped `{opt}` instead of leaving it "
                  f"alongside --drop-D: {forwarded}")
    if '--no-D' in forwarded:
        test_fail(f"restricted rrsync forced --no-D for `{opt}`.  That clears "
                  'preserve_devices/preserve_specials, which also frame the '
                  f'rdev fields, so the file list desynchronises: {forwarded}')

# --- Part 3: the property, not just the option ------------------------------
# Part 2 checks what rrsync forwards.  This checks what that achieves: a push
# of a FIFO through a restricted rrsync must not create one in the tree.
served = base / 'served'
served.mkdir()
# A second wrapper in its own dir: patched_rrsync() always writes
# <workdir>/rrsync-under-test, and the one above is wired to the recording
# stub.  This one drives the real rsync -- through a shim, because RSYNC may
# be a multi-word command (the runner's --protocol=N, or valgrind) while
# rrsync hands its RSYNC to execlp() as a single executable name.
pushwrap = base / 'pushwrap'
pushwrap.mkdir()
shim = base / 'rsync-shim'
shim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' "$@"\n')
shim.chmod(0o755)
rrsync_push = patched_rrsync(pushwrap, rsync_path=str(shim))
rsh = base / 'fake-rsh'
rsh.write_text(
    '#!/bin/sh\n'
    'shift\n'
    'SSH_ORIGINAL_COMMAND="$*"\n'
    'export SSH_ORIGINAL_COMMAND\n'
    'exec %s %s\n' % (shlex.quote(str(rrsync_push)), shlex.quote(str(served))))
rsh.chmod(0o755)

proc = subprocess.run(
    rsync_argv('-rlptgoD', '--specials', '-e', str(rsh), f'{src}/', 'dummy:.'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
# Control: the FIFO's absence only means something if the push otherwise
# worked.  A push that failed outright would "deny" the FIFO too.
landed = served / 'ordinary'
if not landed.is_file() or landed.read_text() != 'PLAIN\n':
    test_fail(f'control failed: the push did not deliver an ordinary file, so '
              f'the absence of the FIFO proves nothing ({ctx})')
if (served / 'evil_fifo').exists():
    test_fail(f'a push through a restricted rrsync created a special file in '
              f'the served tree ({ctx})')
# The claim is "denies creation while the rest proceeds normally", so a
# refusal that also breaks the transfer does not satisfy it.
if proc.returncode != 0:
    test_fail(f'the FIFO was denied, but the transfer itself failed ({ctx})')

# --- Part 3b: a DEVICE, which breaks at every protocol ----------------------
# A device carries its rdev at every protocol version -- unlike a FIFO, whose
# rdev field stops existing at 31 -- so a receiver whose preserve_devices
# disagrees with the sender's desynchronises the file list even on current
# protocols.  This needs no mknod privilege: rsync's own fake-super "%stat"
# xattr is what makes a file a device to rsync, at both ends.
#
# The RECEIVER runs --fake-super too, so it is able to record a device without
# privilege -- which is what makes this an assertion about DENIAL rather than
# about the transfer merely surviving.  Without the wrapper's refusal it lands
# a placeholder named "achar"; with it, the entry is skipped and nothing of
# that name exists.
if xattrs_supported():
    devsrc = base / 'devsrc'
    devsrc.mkdir()
    (devsrc / 'ordinary').write_text('PLAIN\n')
    (devsrc / 'achar').write_bytes(b'')
    try:
        xattr_set(f'{RSYNC_PREFIX}.%stat', '20644 1,3 0:0', devsrc / 'achar')
        made_dev = True
    except Exception:
        made_dev = False

    if made_dev:
        devserved = base / 'devserved'
        devserved.mkdir()
        # Its own wrapper, whose rsync runs --fake-super: patched_rrsync()
        # writes one file per workdir, and the push wrapper above must stay
        # ordinary.
        devwrap = base / 'devwrap'
        devwrap.mkdir()
        devshim = base / 'rsync-shim-fake'
        devshim.write_text('#!/bin/sh\nexec ' + rsync_path_arg(RSYNC) + ' --fake-super "$@"\n')
        devshim.chmod(0o755)
        rrsync_dev = patched_rrsync(devwrap, rsync_path=str(devshim))
        devrsh = base / 'fake-rsh-dev'
        devrsh.write_text(
            '#!/bin/sh\n'
            'shift\n'
            'SSH_ORIGINAL_COMMAND="$*"\n'
            'export SSH_ORIGINAL_COMMAND\n'
            'exec %s %s\n' % (shlex.quote(str(rrsync_dev)),
                               shlex.quote(str(devserved))))
        devrsh.chmod(0o755)
        proc = subprocess.run(
            rsync_argv('--fake-super', '-rlptgoD', '--specials', '-e',
                       str(devrsh), f'{devsrc}/', 'dummy:.'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=120)
        ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
        landed = devserved / 'ordinary'
        if not landed.is_file() or landed.read_text() != 'PLAIN\n':
            test_fail(f'control failed: pushing a tree containing a device did '
                      f'not deliver the ordinary file beside it, so nothing '
                      f'below proves anything.  Clearing preserve_devices on '
                      f'the receiver alone desynchronises the file list at '
                      f'EVERY protocol, which fails the whole transfer ({ctx})')
        if os.path.lexists(devserved / 'achar'):
            test_fail(f'a push through a restricted rrsync recorded a device in '
                      f'the served tree ({ctx})')
        if proc.returncode != 0:
            test_fail(f'the device was denied, but the transfer itself failed '
                      f'({ctx})')

# --- Part 4: and NOT on the sending side ------------------------------------
# Forcing it there denies nothing (a sender creates nothing) while breaking the
# file list, because only one end of the connection gets the option.
for opt in ('-D', '--specials'):
    forwarded = forwarded_opts(f'rsync --server --sender {opt} . .')
    for forced in ('--drop-D', '--no-D'):
        if forced in forwarded:
            test_fail(f"restricted rrsync forced {forced} for `{opt}` on the "
                      'SENDING side, where there is nothing to deny: a sender '
                      'creates no device or special entry in the served tree '
                      f'({forwarded})')

print("rrsync-specials: --specials creates special files in the served tree; "
      "a restricted rrsync forces --drop-D on the receiving side (a push can "
      "create neither a special nor a device, and the transfer still "
      "succeeds) and leaves the sending side alone")
