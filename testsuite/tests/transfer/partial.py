#!/usr/bin/env python3

import os
import signal
import subprocess
import time

from harness.rsync import (
    FROMDIR, SCRATCHDIR, TODIR,
    assert_same, make_data_file, makepath, rmtree, rsync_argv, run_rsync,
    test_fail,
)

src = FROMDIR
deepdir = os.path.join('d1', 'd2', 'd3')
deep = os.path.join(deepdir, 'f3')
FULL = 12_000_000

def seed_big():
    rmtree(src)
    rmtree(TODIR)
    makepath(src / deepdir)
    make_data_file(src / deep, FULL)

def is_prefix(partial) -> bool:
    pb = partial.read_bytes()
    return 0 < len(pb) < FULL and (src / deep).read_bytes()[:len(pb)] == pb

def interrupt_transfer(extra_args, partial_path):
    proc = subprocess.Popen(
        rsync_argv('-a', '--no-whole-file', '--bwlimit=400', *extra_args,
                   f'{src}/', f'{TODIR}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tdir = TODIR / deepdir
    caught = False
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if tdir.is_dir():
            temps = [p for p in tdir.glob('.f3.*')
                     if p.is_file() and p.stat().st_size > 0]
            if temps:
                caught = True
                break
        time.sleep(0.02)
    proc.send_signal(signal.SIGTERM)
    proc.wait()
    if not caught:
        test_fail("never caught an in-progress temp (transfer finished too "
                  "fast to interrupt)")
    pdeadline = time.monotonic() + 5
    while time.monotonic() < pdeadline:
        if partial_path.is_file() and partial_path.stat().st_size > 0:
            return
        time.sleep(0.05)

seed_big()
interrupt_transfer(['--partial'], TODIR / deep)
if not (TODIR / deep).is_file() or not is_prefix(TODIR / deep):
    test_fail("--partial did not leave a valid partial in the dest file")
run_rsync('-a', '--partial', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='--partial resume')

rmtree(src)
rmtree(TODIR)
makepath(src / deepdir, TODIR / deepdir / '.rsync-partial')
make_data_file(src / deep, 1_000_000)
full = (src / deep).read_bytes()
(TODIR / deepdir / '.rsync-partial' / 'f3').write_bytes(full[:400_000])
run_rsync('-a', '--partial-dir=.rsync-partial', '--no-whole-file',
          f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='rel partial-dir preseed')
if (TODIR / deepdir / '.rsync-partial').exists():
    test_fail("relative --partial-dir not removed after the partial was used")

seed_big()
part = TODIR / deepdir / '.rsync-partial' / 'f3'
interrupt_transfer(['--partial-dir=.rsync-partial'], part)
if not part.is_file() or not is_prefix(part):
    test_fail("relative --partial-dir did not keep a valid partial at depth")
run_rsync('-a', '--partial-dir=.rsync-partial', '--no-whole-file',
          f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='rel partial-dir resume')

ext = SCRATCHDIR / 'partials'
rmtree(ext)
ext.mkdir()
seed_big()
interrupt_transfer([f'--partial-dir={ext}'], ext / 'f3')
if not (ext / 'f3').is_file() or not is_prefix(ext / 'f3'):
    test_fail("absolute --partial-dir did not write the partial to the "
              "outside-tree dir")

rmtree(src)
rmtree(TODIR)
rmtree(ext)
makepath(src / deepdir, ext)
make_data_file(src / deep, 1_000_000)
(ext / 'f3').write_bytes((src / deep).read_bytes()[:400_000])
run_rsync('-a', f'--partial-dir={ext}', '--no-whole-file', f'{src}/', f'{TODIR}/')
assert_same(TODIR / deep, src / deep, label='abs partial-dir delta resume')
if (ext / 'f3').exists():
    test_fail("absolute --partial-dir basis was not consumed after a "
              "successful delta resume")

print("partial: --partial + relative/absolute --partial-dir verified at depth")
