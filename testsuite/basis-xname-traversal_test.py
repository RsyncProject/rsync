#!/usr/bin/env python3
# Server-controlled alternate-basis xname must stay confined to the operator
# basedir on the client.  Reported by z3r0s (github z3r0s6), 2026-07.
#
# When the client pulls with an alt-dest flag (--link-dest / --copy-dest /
# --compare-dest, or --fuzzy), the sender sends an fnamecmp_type + xname naming
# which alternate basis the receiver should reconstruct against.  The receiver
# joins the wire-supplied xname to the operator's basedir (basis_dir[]) and
# opens it as the delta basis.  read_ndx_and_attrs() sanitized xname only when
# sanitize_paths was set (daemon side); a *client* receiver has
# sanitize_paths == 0, so a malicious server could send xname="../../etc/shadow"
# and make the client open an out-of-tree file (client-side arbitrary-read /
# file-existence-oracle / FIFO-hang).  secure_basis_open()'s ownership walk does
# not stop this: it deliberately follows a plain ".." to a regular file (the
# legitimate --link-dest=../01 sibling, #915) and only refuses foreign-owned
# *symlink* components.  The fix sanitizes the wire xname itself (for basis
# types only, leaving the hard-link "=> target" xname alone).
#
# Test: build an instrumented daemon-sender (env-gated sender.c edit that, when
# RSYNC_MAL_XNAME is set, injects ITEM_XNAME_FOLLOWS|ITEM_BASIS_TYPE_FOLLOWS +
# fnamecmp_type=FNAMECMP_FUZZY+1 (== basis_dir[0]) + xname onto each transfer),
# run it via RSYNC_CONNECT_PROG with the production rsync as the receiver pulling
# with --link-dest, and observe where the receiver opens the basis.
#
# Two FIFOs, each with a helper blocked in open(O_WRONLY) that drops a flag when
# some reader opens it, tell RED from GREEN without hanging the receiver (it
# reads EOF and finishes):
#   * ESCAPE  base/secret            reached only by an unsanitized "../secret"
#   * DECOY   linkdest/secret        where the SANITIZED "secret" lands
# Injected xname is "../secret":
#   - vulnerable receiver opens ESCAPE  -> escape flag  -> FAIL (traversal)
#   - fixed receiver sanitizes to "secret", opens DECOY -> decoy flag -> PASS
#     (the decoy flag also proves the crafted xname actually crossed the wire,
#      so a stale/failed injection build can't false-PASS as "confined")
#   - neither flag -> the injection never took effect -> FAIL (vacuous)

import os
import shlex
import subprocess
import time
from pathlib import Path
import sys

from rsyncfns import (
    SCRATCHDIR, build_patched_rsync, forced_protocol, makepath, rmtree,
    rsync_argv, test_fail, test_skipped, write_daemon_conf,
)

# Gates ----------------------------------------------------------------------
# xname only exists in the protocol >= 29 item stream; below that
# write_ndx_and_attrs() returns before any flags/xname, so the injection can't
# cross the wire and the test would be vacuous.  (The fix is a no-op there.)
_proto = forced_protocol()
if _proto is not None and _proto < 29:
    test_skipped("basis-xname-traversal: xname/item flags need protocol >= 29")
if not hasattr(os, 'mkfifo'):
    test_skipped("basis-xname-traversal: os.mkfifo unavailable on this platform")


# -- Build the instrumented sender (shared helper: Cygwin skip, CCACHE_DISABLE,
#    forced rebuild of the patched unit) -------------------------------------
PATCH_OLD = ("\t\twrite_ndx_and_attrs(f_out, ndx, iflags, fname, file, fnamecmp_type, xname, xlen);\n"
             "\t\twrite_sum_head(f_xfer, s);")
PATCH_NEW = ("\t\tif (getenv(\"RSYNC_MAL_XNAME\")) { /* basis-xname-traversal PoC */\n"
             "\t\t\tiflags |= ITEM_XNAME_FOLLOWS | ITEM_BASIS_TYPE_FOLLOWS;\n"
             "\t\t\tfnamecmp_type = FNAMECMP_FUZZY + 1;\n"
             "\t\t\txlen = strlcpy(xname, getenv(\"RSYNC_MAL_XNAME\"), MAXPATHLEN);\n"
             "\t\t}\n"
             "\t\twrite_ndx_and_attrs(f_out, ndx, iflags, fname, file, fnamecmp_type, xname, xlen);\n"
             "\t\twrite_sum_head(f_xfer, s);")
mal_rsync = build_patched_rsync('mal-xname-rsync', [('sender.c', PATCH_OLD, PATCH_NEW)])


# -- Workspace ----------------------------------------------------------------
#   base/serversrc/file    the file the instrumented daemon offers
#   base/linkdest/         the client's --link-dest (basis_dir[0])
#   base/linkdest/secret   DECOY fifo  -- where a sanitized "secret" resolves
#   base/secret            ESCAPE fifo -- where an unsanitized "../secret" lands
#   base/dest/             the client's destination
base = SCRATCHDIR / 'xname-race'
rmtree(base)
serversrc = base / 'serversrc'
linkdest = base / 'linkdest'
dest = base / 'dest'
escape = base / 'secret'                 # linkdest/../secret
decoy = linkdest / 'secret'              # linkdest/secret
esc_flag = base / 'escape.flag'
dec_flag = base / 'decoy.flag'
makepath(serversrc)
makepath(linkdest)
makepath(dest)
(serversrc / 'file').write_text("from the server\n")


# A helper that blocks in open(fifo, O_WRONLY) until some reader opens the FIFO,
# then records the flag.  Terminated below if no reader ever appears.
WRITER = ("import os,sys\n"
          "open(sys.argv[3],'w').close()\n"   # ready: about to block in open()
          "fd=os.open(sys.argv[1],os.O_WRONLY)\n"
          "open(sys.argv[2],'w').close()\n"
          "os.close(fd)\n")


def spawn(fifo, flag):
    ready = Path(str(flag) + '.ready')
    if ready.exists():
        ready.unlink()
    proc = subprocess.Popen(
        [sys.executable, '-c', WRITER, str(fifo), str(flag), str(ready)])
    # Wait until the helper is actually at its blocking open().  Starting the
    # transfer before that lets the receiver come and go while nothing is
    # watching the FIFO, and the run reports a vacuous result -- which is what
    # made this test flaky on the slower fleet VMs.
    deadline = time.time() + 30
    while not ready.exists() and proc.poll() is None and time.time() < deadline:
        time.sleep(0.02)
    return proc


def settle(w):
    """Give a rendezvoused helper a bounded chance to record its flag; a still-
    blocked one just times out.  (Closes the terminate-before-flag race.)"""
    try:
        w.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def reap(w):
    if w.poll() is None:
        w.terminate()
        try:
            w.wait(timeout=10)
        except subprocess.TimeoutExpired:
            w.kill()
            w.wait()


def attempt():
    """One injection run.  Returns the receiver's CompletedProcess.

    Re-creates the FIFOs and flags each time so a retry starts clean.
    """
    for f in (escape, decoy, esc_flag, dec_flag):
        if os.path.lexists(f):
            os.unlink(f)
    rmtree(dest)
    makepath(dest)
    os.mkfifo(escape)
    os.mkfifo(decoy)
    esc_w = spawn(escape, esc_flag)
    dec_w = spawn(decoy, dec_flag)
    proc = None
    try:
        conf = write_daemon_conf(
            [('m', {'path': str(serversrc), 'read only': 'yes', 'use chroot': 'no'})],
            name='mal-xname-rsyncd.conf')
        os.environ['RSYNC_CONNECT_PROG'] = f'{shlex.quote(str(mal_rsync))} --config={shlex.quote(str(conf))} --daemon'
        os.environ['RSYNC_MAL_XNAME'] = '../secret'  # from basis_dir[0] == linkdest
        proc = subprocess.run(
            rsync_argv('-a', f'--link-dest={linkdest}',
                       'rsync://localhost/m/file', str(dest) + '/'),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        settle(esc_w)
        settle(dec_w)
    finally:
        os.environ.pop('RSYNC_MAL_XNAME', None)
        os.environ.pop('RSYNC_CONNECT_PROG', None)
        reap(esc_w)
        reap(dec_w)
        for f in (escape, decoy):
            if os.path.lexists(f):
                os.unlink(f)
    return proc


# A run where NEITHER fifo was opened proves nothing: the injection did not
# take effect, so there was no traversal attempt to confine.  That is a setup
# failure, not a security signal, and on the slower fleet VMs it happens often
# enough to make the test unusable -- so retry it.  An ESCAPE is never retried:
# the loop stops the moment the escape flag appears.
attempts = 0
for _try in range(6):
    attempts += 1
    proc = attempt()
    if esc_flag.is_file() or dec_flag.is_file():
        break


# -- Oracle -------------------------------------------------------------------
out_tail = '\n'.join((proc.stdout if proc else '').splitlines()[-20:])

if esc_flag.is_file():
    test_fail(
        "malicious server traversed the client's filesystem via the alt-dest "
        f"xname: the receiver opened {escape} (one level above the --link-dest "
        "dir) as the delta basis.  A server-supplied xname of '../secret' was "
        "not sanitized on the client (sanitize_paths==0 off-daemon).  Fix: "
        "sanitize a basis-type xname in read_ndx_and_attrs().  Receiver output "
        f"tail:\n{out_tail}")

# The decoy flag proves the crafted xname reached the receiver AND was confined
# to the basedir (sanitized "../secret" -> "secret" -> linkdest/secret).  Its
# absence means the injection never took effect (e.g. a stale patched build),
# so a clear escape flag alone would be a vacuous pass.
if not dec_flag.is_file():
    test_fail(
        "the crafted xname never reached the receiver's basis open (neither the "
        "escape nor the decoy FIFO was opened) -- the instrumented-sender "
        f"injection did not take effect, so this run is vacuous after "
        f"{attempts} attempt(s).  This is a harness failure, NOT a traversal: "
        "an escape is reported separately and is never retried.  Receiver rc="
        f"{proc.returncode if proc else 'n/a'}.  Output tail:\n{out_tail}")

if proc.returncode != 0:
    test_fail(
        f"xname confined to the basedir, but the pull failed (rc={proc.returncode}).  "
        f"Output tail:\n{out_tail}")

print("basis-xname-traversal: receiver confined the server-supplied alt-dest "
      "xname to the --link-dest dir; '../secret' resolved to linkdest/secret, "
      "not the out-of-tree sibling.")
