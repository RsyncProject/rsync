#!/usr/bin/env python3

import os
import shlex
import subprocess

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, build_patched_rsync, forced_protocol, makepath, rmtree, rsync_argv_for,
    test_fail, test_skipped,
)

_proto = forced_protocol()
if _proto is not None and _proto < 29:
    test_skipped("basis-xname-traversal: xname/item flags need protocol >= 29")
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

base = SCRATCHDIR / 'xname-race'
rmtree(base)
serversrc = base / 'serversrc'
linkdest = base / 'linkdest'
dest = base / 'dest'
escape = base / 'secret'
decoy = linkdest / 'secret'
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
os.environ['RSYNC_MAL_XNAME'] = '../secret'
os.environ['RSYNC_BASIS_TRACE'] = str(trace_file)
try:
    argv = rsync_argv_for(mal_rsync, '-a', f'--link-dest={linkdest}',
                          'rsync://localhost/m/file', str(dest) + '/')
    proc = subprocess.run(
        argv,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
finally:
    os.environ.pop('RSYNC_BASIS_TRACE', None)
    os.environ.pop('RSYNC_MAL_XNAME', None)
    os.environ.pop('RSYNC_CONNECT_PROG', None)

out_tail = '\n'.join(proc.stdout.splitlines()[-20:])
trace = trace_file.read_text().splitlines() if trace_file.is_file() else []
escaped = f'{linkdest}\t../secret'
confined = f'{linkdest}\tsecret'

if escaped in trace:
    test_fail(
        f"server-supplied alt-dest name escaped to {escape}\n{out_tail}")

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
