#!/usr/bin/env python3
"""Coverage: cheap one-off paths no other test reaches.

  usage.c daemon_usage + help-rsyncd.h     -- rsync --daemon --help
  main.c show_malloc_stats /
  flist.c show_flist_stats                 -- --info=stats3
  util2.c sum_as_hex /
  checksum.c canonical_checksum            -- -c --out-format='%C %n'
                                              (always_checksum branch) and
                                              --out-format='%C %n' alone
                                              (ITEM_TRANSFER branch)
  generator.c start_delete_delay_temp /
  flush_delete_delay                       -- --delete-delay with enough
                                              queued deletions to overflow
                                              the BIGPATHBUFLEN*4 in-memory
                                              buffer into the temp-file path

Each section asserts the observable behaviour, not just exit==0, so the test
also guards against regressions in those paths.
"""

import re
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail,
)


def run(*argv, ok=(0,)):
    r = subprocess.run(rsync_argv(*argv), capture_output=True, text=True)
    if r.returncode not in ok:
        test_fail(f"rsync {' '.join(argv)} -> rc={r.returncode}\n{r.stderr}")
    return r


# --- 1. daemon_usage + help-rsyncd.h ----------------------------------------
r = run('--daemon', '--help')
if '--no-detach' not in r.stdout or 'rsync --daemon' not in r.stdout:
    test_fail(f"--daemon --help did not emit the daemon usage text:\n{r.stdout}")


# --- 2. show_malloc_stats + show_flist_stats (INFO_GTE(STATS,3)) ------------
src = SCRATCHDIR / 'stats-src'
dst = SCRATCHDIR / 'stats-dst'
rmtree(src); rmtree(dst); makepath(src, dst)
for i in range(3):
    (src / f'f{i}').write_bytes(b'x' * (64 + i))
r = run('-r', '--info=stats3', f'{src}/', f'{dst}/')
out = r.stdout + r.stderr
# show_malloc_stats prints an arena/heap summary (one block per process), but
# only on a glibc/mallinfo build -- on the BSDs/macOS show_malloc_stats() is a
# no-op (still called, and thus covered, at INFO_GTE(STATS,3), just no output).
# show_flist_stats() is currently an empty stub.
import platform
if platform.system() == 'Linux':
    if 'heap statistics' not in out:
        test_fail(f"--info=stats3 did not emit malloc stats:\n{out}")
    # One block per process: sender + server receiver + server generator.
    if out.count('heap statistics') < 3:
        test_fail(f"--info=stats3 emitted fewer than 3 heap-stat blocks:\n{out}")


# --- 3. sum_as_hex + canonical_checksum (--out-format=%C) --------------------
csrc = SCRATCHDIR / 'csum-src'
cdst = SCRATCHDIR / 'csum-dst'
rmtree(csrc); rmtree(cdst); makepath(csrc, cdst)
payload = b'hello, checksum coverage\n'
(csrc / 'a').write_bytes(payload)
(csrc / 'b').write_bytes(payload + b'!')

# The file/transfer checksum algorithm is negotiated (xxh128/xxh3/md5/...
# depending on build + protocol), so don't assert which -- just that %C emits
# a per-file hex digest of plausible length, and that DIFFERENT file content
# produces a DIFFERENT digest (i.e. it's the actual sum, not a placeholder).
HEX = re.compile(r'^([0-9a-f]{16,128}) (\S+)$', re.M)

def check_C(out, label):
    # Two valid outcomes: hex digests (canonical csum -> sum_as_hex returns the
    # hex string) or a run of spaces (non-canonical, e.g. md4 at --protocol=29
    # -> sum_as_hex returns NULL and log.c emits sum_len*2 spaces).  Either way
    # sum_as_hex/canonical_checksum were CALLED, which is the coverage goal.
    sums = dict((m[2], m[1]) for m in HEX.finditer(out))
    if sums:
        if 'a' not in sums or 'b' not in sums or sums['a'] == sums['b']:
            test_fail(f"{label}: per-file digests not distinct:\n{out!r}")
        return 'hex'
    if not re.search(r'^ {8,} a$', out, re.M):
        test_fail(f"{label}: emitted neither a hex digest nor the "
                  f"non-canonical-csum spaces fallback:\n{out!r}")
    return 'spaces'

# 3a. always_checksum branch (log.c:707): with -c, %C encodes F_SUM(file).
r = run('-rc', '--out-format=%C %n', f'{csrc}/', f'{cdst}/')
kind_a = check_C(r.stdout, '-c %C')

# 3b. ITEM_TRANSFER branch (log.c:709): no -c, %C encodes sender_file_sum.
rmtree(cdst); makepath(cdst)
r = run('-r', '--out-format=%C %n', f'{csrc}/', f'{cdst}/')
kind_b = check_C(r.stdout, '%C (no -c)')


# --- 4. start_delete_delay_temp + flush_delete_delay ------------------------
# deldelay_size = BIGPATHBUFLEN*4 = 20480.  Each remember_delete() entry is
# "%x %s\0" -> ~6 + len(name).  300 files with 80-char names -> ~25 KB,
# guaranteed to overflow the in-memory buffer and spill to the temp file.
ddsrc = SCRATCHDIR / 'dd-src'
dddst = SCRATCHDIR / 'dd-dst'
rmtree(ddsrc); rmtree(dddst); makepath(ddsrc, dddst)
(ddsrc / 'keep').write_bytes(b'k')
NAME = 'D' * 80
N = 300
for i in range(N):
    (dddst / f'{NAME}{i:04d}').write_bytes(b'x')
r = run('-r', '--delete-delay', f'{ddsrc}/', f'{dddst}/')
remaining = [p for p in dddst.iterdir() if p.name.startswith(NAME)]
if remaining:
    test_fail(f"--delete-delay left {len(remaining)}/{N} files "
              f"after the temp-file spill path")
if not (dddst / 'keep').is_file():
    test_fail("--delete-delay lost the kept file")


print(f"misc-coverage: --daemon --help, --info=stats3, "
      f"%C ({kind_a}/{kind_b}), --delete-delay temp-file spill ({N} entries) ok")
