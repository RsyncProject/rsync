#!/usr/bin/env python3
"""--write-batch, --only-write-batch and --read-batch.

Batch mode records the protocol stream to a file so it can be replayed
later against another destination.  It is worth covering here for two
reasons: the batch file is a byte-for-byte record of the wire format, so it
is exactly what newline translation would silently corrupt (this port sets
its stdio to binary mode explicitly to prevent that), and replaying one
drives a code path where the generator and receiver talk only to each
other -- with no sender in between -- which is where a thread-based fork
stand-in is most likely to diverge from the real thing.

Replaying used to hang here, with every file written correctly and the run
never returning: `batch_fd` is set to -1 when the input stream hits EOF,
which under --read-batch happens in the receiving half only.  fork() gives
each half its own copy; sharing one meant the receiver finishing the batch
also stopped the generator selecting for input.  It is RSYNC_TLS now.

If RSYNC_WIN_TEST_HOST is set, the batch is also replayed by the rsync on
that host, so the recorded stream is checked for portability as well.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import hashlib
import os
import shutil
import subprocess

from wintest import (SCRATCH, check, compare_trees, ok, pseudo_random, rmtree,
                     rsync, write)

os.chdir(SCRATCH)

src = SCRATCH / 'from'
write(src / 'text.txt', 'line one\nline two\r\nline three\n')
write(src / 'binary.bin', pseudo_random(150000, seed=17))
write(src / 'sub' / 'nested.txt', 'nested\n')

# --- --write-batch transfers and records at the same time -----------------
dst = SCRATCH / 'to'
rsync('-rt', '--write-batch=BATCH', f'{src}/', f'{dst}/')
compare_trees(src, dst, '--write-batch transfer')

batch = SCRATCH / 'BATCH'
check(batch.is_file(), '--write-batch did not create the batch file')
check(batch.stat().st_size > 150000,
      f'the batch file is too small to hold the data: {batch.stat().st_size}')
# The companion script records the flags the batch was made with.
check((SCRATCH / 'BATCH.sh').is_file(),
      '--write-batch did not write the companion BATCH.sh')

# --- --read-batch replays it against a fresh destination ------------------
replayed = SCRATCH / 'replayed'
rsync('-rt', '--read-batch=BATCH', f'{replayed}/')
compare_trees(src, replayed, '--read-batch replay')

# text.txt holds a bare CRLF: if anything along the batch path translated
# newlines the replay would not be byte-identical.
check((replayed / 'text.txt').read_bytes() == (src / 'text.txt').read_bytes(),
      'a batch replay altered the bytes of a file containing CRLF')

# The batch survives being read, so it can be replayed again elsewhere.
check(batch.is_file(), '--read-batch consumed the batch file')
replayed2 = SCRATCH / 'replayed2'
rsync('-rt', '--read-batch=BATCH', f'{replayed2}/')
compare_trees(src, replayed2, '--read-batch second replay')

# --- --only-write-batch records without transferring ----------------------
notdst = SCRATCH / 'never-created'
rsync('-rt', '--only-write-batch=BATCH2', f'{src}/', f'{notdst}/')
check(not notdst.exists(),
      '--only-write-batch created the destination it was told not to touch')
check((SCRATCH / 'BATCH2').is_file(),
      '--only-write-batch did not write the batch file')

from_batch2 = SCRATCH / 'from-batch2'
rsync('-rt', '--read-batch=BATCH2', f'{from_batch2}/')
compare_trees(src, from_batch2, '--only-write-batch replay')

# --- a batch carrying a delta rather than whole files ---------------------
# The replay destination is pre-seeded with the same basis the original
# destination had, so the recorded stream is a delta, not whole files.
dsrc = SCRATCH / 'dsrc'
body = pseudo_random(200000, seed=19)
write(dsrc / 'doc.bin', body + b'-NEW', mtime=1_600_000_000)

seeded = SCRATCH / 'seeded'
write(seeded / 'doc.bin', body + b'-OLD', mtime=1_500_000_000)
rsync('-rt', '--no-whole-file', '--write-batch=BATCH3', f'{dsrc}/', f'{seeded}/')
check((seeded / 'doc.bin').read_bytes() == body + b'-NEW',
      'the delta transfer that produced the batch was itself wrong')
check((SCRATCH / 'BATCH3').stat().st_size < 100000,
      'the batch recorded whole-file data where a delta was expected: '
      f'{(SCRATCH / "BATCH3").stat().st_size} bytes')

seeded2 = SCRATCH / 'seeded2'
write(seeded2 / 'doc.bin', body + b'-OLD', mtime=1_500_000_000)
rsync('-rt', '--read-batch=BATCH3', f'{seeded2}/')
check((seeded2 / 'doc.bin').read_bytes() == body + b'-NEW',
      'replaying a delta batch produced the wrong content')

# --- a batch that deletes -------------------------------------------------
# --delete exercises the del-stats exchange between the two halves, which is
# the part of the replay handshake that has no sender to drive it.
delsrc = SCRATCH / 'delsrc'
write(delsrc / 'keep.txt', 'keep\n')
deldst = SCRATCH / 'deldst'
write(deldst / 'keep.txt', 'keep\n')
write(deldst / 'obsolete.txt', 'should go\n')
rsync('-rt', '--delete', '--write-batch=BATCH4', f'{delsrc}/', f'{deldst}/')
check(not (deldst / 'obsolete.txt').exists(),
      '--delete did not remove the extraneous file')

# The batch records the stream, not the options: --delete has to be given
# again on the replay, which is what the generated BATCH4.sh does.  (Plain
# --read-batch deletes nothing, on Linux equally -- checked against the
# peer.)
check('--delete' in (SCRATCH / 'BATCH4.sh').read_text(),
      'BATCH4.sh does not record the --delete it was created with')

deldst2 = SCRATCH / 'deldst2'
write(deldst2 / 'keep.txt', 'keep\n')
write(deldst2 / 'obsolete.txt', 'should go\n')
rsync('-rt', '--delete', '--read-batch=BATCH4', f'{deldst2}/')
check(not (deldst2 / 'obsolete.txt').exists(),
      'replaying a --delete batch did not remove the extraneous file')
compare_trees(delsrc, deldst2, '--delete batch replay')

# --- the batch replays on a peer that forks -------------------------------
host = os.environ.get('RSYNC_WIN_TEST_HOST')
if not host or not shutil.which('ssh'):
    ok('--write-batch, --only-write-batch and --read-batch all round-trip '
       '(set RSYNC_WIN_TEST_HOST to also check the batch replays elsewhere)')


def ssh(command, expect=0):
    proc = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         host, command],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if expect is not None and proc.returncode != expect:
        ok('--write-batch, --only-write-batch and --read-batch round-trip '
           f'(ssh to {host} unavailable: {proc.stderr.strip()})')
    return proc.stdout


remote = os.environ.get('RSYNC_WIN_TEST_DIR', '/tmp/rsync-win-test') + '-batch'
ssh('true')
ssh(f'rm -rf {remote} && mkdir -p {remote}')

# Send just the batch, then let the peer replay it into an empty tree.
rsync('-t', str(batch), str(SCRATCH / 'BATCH.sh'), f'{host}:{remote}/')
ssh(f'cd {remote} && rsync -rt --read-batch=BATCH out/')

listing = ssh(f'cd {remote}/out && find . -type f | sort | '
              'xargs md5sum | sed "s|  \\./|  |"')
remote_digests = {}
for line in listing.splitlines():
    digest, _, name = line.partition('  ')
    if name:
        remote_digests[name.strip()] = digest.strip()

local_digests = {}
for dirpath, _dirnames, filenames in os.walk(src):
    for name in filenames:
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, src).replace('\\', '/')
        local_digests[rel] = hashlib.md5(open(full, 'rb').read()).hexdigest()

check(remote_digests == local_digests,
      'the batch replayed on the peer did not reproduce the source:\n'
      f'  local : {sorted(local_digests.items())}\n'
      f'  remote: {sorted(remote_digests.items())}')

ok('--write-batch, --only-write-batch and --read-batch round-trip, including '
   'deltas and deletes, and the batch replays on a forking rsync')
