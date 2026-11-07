#!/usr/bin/env python3

import os

from harness.rsync import SCRATCHDIR, run_rsync, test_fail, test_skipped

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
        pass
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
