#!/usr/bin/env python3
# Regression for KI-51/52: the log file must escape control characters in a
# (possibly attacker-controlled) filename, so an admin who cat's the log can't
# have terminal escapes injected (CWE-117).  Covers both C0 (< 0x20) and the C1
# range (0x80-0x9f, incl CSI 0x9b) on the log path.

import os

from rsyncfns import SCRATCHDIR, run_rsync, test_fail, test_skipped

base = SCRATCHDIR / 'logctl'
src = base / 'src'
dst = base / 'dst'
src.mkdir(parents=True, exist_ok=True)
dst.mkdir(parents=True, exist_ok=True)
log = base / 'rsync.log'

srcb = os.fsencode(str(src))
made = 0
for raw in (b'c0_\x1b_esc', b'c1_\x9b_csi'):
    try:
        with open(srcb + b'/' + raw, 'wb') as fh:
            fh.write(b'x')
        made += 1
    except OSError:
        pass  # a filesystem that rejects control-char names (e.g. Cygwin)
if made == 0:
    test_skipped("filesystem rejects control-char filenames")

run_rsync('-rv', f'--log-file={log}', f'{src}/', f'{dst}/')

data = log.read_bytes()
if b'\x1b' in data:
    test_fail("raw C0 ESC (0x1b) byte left un-escaped in the log file")
if b'\x9b' in data:
    test_fail("raw C1 CSI (0x9b) byte left un-escaped in the log file")
if b'\\#' not in data:
    test_fail("expected escaped \\#NNN sequences in the log file, found none")

print(f'log-control-chars: {made} control-char name(s) escaped in the log file')
