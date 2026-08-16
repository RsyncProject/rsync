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
# Test: build an instrumented rsync (env-gated sender.c edit that, when
# RSYNC_MAL_XNAME is set, injects ITEM_XNAME_FOLLOWS|ITEM_BASIS_TYPE_FOLLOWS +
# fnamecmp_type=FNAMECMP_FUZZY+1 (== basis_dir[0]) + xname onto each transfer).
# An env-gated receiver.c edit records the exact basedir and relpath passed to
# secure_basis_open().  This observes the security decision directly without
# relying on timing-sensitive FIFO rendezvous behaviour across operating systems.

import os
import shlex
import subprocess

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
# -- Build the instrumented peer (shared helper: Cygwin skip, CCACHE_DISABLE,
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
TRACE_OLD = ("static int secure_basis_open(const char *basedir, const char *relpath, int flags, mode_t mode)\n"
             "{\n"
             "\textern int am_daemon, am_chrooted;")
TRACE_NEW = ("static int secure_basis_open(const char *basedir, const char *relpath, int flags, mode_t mode)\n"
             "{\n"
             "\tconst char *trace_path = getenv(\"RSYNC_BASIS_TRACE\");\n"
             "\tif (trace_path) {\n"
             "\t\tFILE *trace = fopen(trace_path, \"a\");\n"
             "\t\tif (trace) {\n"
             "\t\t\tfprintf(trace, \"%s\\t%s\\n\", basedir ? basedir : \"\", relpath);\n"
             "\t\t\tfclose(trace);\n"
             "\t\t}\n"
             "\t}\n"
             "\textern int am_daemon, am_chrooted;")
mal_rsync = build_patched_rsync(
    'mal-xname-rsync',
    [('sender.c', PATCH_OLD, PATCH_NEW),
     ('receiver.c', TRACE_OLD, TRACE_NEW)],
)


# -- Workspace ----------------------------------------------------------------
#   base/serversrc/file    the file the instrumented daemon offers
#   base/linkdest/         the client's --link-dest (basis_dir[0])
#   base/linkdest/secret   where a sanitized "secret" resolves
#   base/secret            where an unsanitized "../secret" resolves
#   base/dest/             the client's destination
base = SCRATCHDIR / 'xname-race'
rmtree(base)
serversrc = base / 'serversrc'
linkdest = base / 'linkdest'
dest = base / 'dest'
escape = base / 'secret'                 # linkdest/../secret
decoy = linkdest / 'secret'              # linkdest/secret
trace_file = base / 'basis.trace'
makepath(serversrc)
makepath(linkdest)
makepath(dest)
(serversrc / 'file').write_text("from the server\n")
escape.write_text("escaped basis\n")
decoy.write_text("confined basis\n")

conf = write_daemon_conf(
    [('m', {'path': str(serversrc), 'read only': 'yes', 'use chroot': 'no'})],
    name='mal-xname-rsyncd.conf')
os.environ['RSYNC_CONNECT_PROG'] = f'{shlex.quote(str(mal_rsync))} --config={shlex.quote(str(conf))} --daemon'
os.environ['RSYNC_MAL_XNAME'] = '../secret'  # from basis_dir[0] == linkdest
os.environ['RSYNC_BASIS_TRACE'] = str(trace_file)
try:
    argv = rsync_argv('-a', f'--link-dest={linkdest}',
                      'rsync://localhost/m/file', str(dest) + '/')
    argv[0] = str(mal_rsync)
    proc = subprocess.run(
        argv,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
finally:
    os.environ.pop('RSYNC_BASIS_TRACE', None)
    os.environ.pop('RSYNC_MAL_XNAME', None)
    os.environ.pop('RSYNC_CONNECT_PROG', None)


# -- Oracle -------------------------------------------------------------------
out_tail = '\n'.join(proc.stdout.splitlines()[-20:])
trace = trace_file.read_text().splitlines() if trace_file.is_file() else []
escaped = f'{linkdest}\t../secret'
confined = f'{linkdest}\tsecret'

if escaped in trace:
    test_fail(
        "malicious server traversed the client's filesystem via the alt-dest "
        f"xname: the receiver attempted {escape} (one level above the --link-dest "
        "dir) as the delta basis.  A server-supplied xname of '../secret' was "
        "not sanitized on the client (sanitize_paths==0 off-daemon).  Fix: "
        "sanitize a basis-type xname in read_ndx_and_attrs().  Receiver output "
        f"tail:\n{out_tail}")

# The trace proves the crafted xname reached the receiver and was confined to
# the basedir (sanitized "../secret" -> "secret" -> linkdest/secret).  Its
# absence means the injection never took effect (e.g. a stale patched build).
if confined not in trace:
    test_fail(
        "the crafted xname never reached the receiver's confined basis open; "
        "the instrumented injection did not take effect, so this run is "
        f"vacuous. Trace={trace!r}. Receiver rc={proc.returncode}. "
        f"Output tail:\n{out_tail}")

if proc.returncode != 0:
    test_fail(
        f"xname confined to the basedir, but the pull failed (rc={proc.returncode}).  "
        f"Output tail:\n{out_tail}")

print("basis-xname-traversal: receiver confined the server-supplied alt-dest "
      "xname to the --link-dest dir; '../secret' resolved to linkdest/secret, "
      "not the out-of-tree sibling.")
