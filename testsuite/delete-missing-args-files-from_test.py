#!/usr/bin/env python3
# Functional regression: --delete-missing-args with --files-from aborts the
# transfer with "invalid file mode 00 ... protocol incompatibility (code 2)"
# instead of deleting the entries that are missing on the sender.
#
# Reported as #910 ("Security fix in flist.c breaks --delete-missing-args with
# --files-from").
#
# Root cause: for a --files-from entry that does not exist on the sender,
# --delete-missing-args==2 deliberately sends a "missing" file entry with
# mode == 0 (the generator's signal to delete it on the receiver).  The 3.4.x
# security mode-validation added to recv_file_entry() (flist.c) rejects mode 0
# as an invalid file type BEFORE the generator can act on it, so the receiver
# bails out with a protocol error and nothing is deleted.  Works in 3.4.1.
#
# Two scenarios, since a missing FILE and a missing DIRECTORY are sent as
# distinct mode-0 entries:
#   * a regular file present on the receiver but absent on the sender, and
#   * a directory present on the receiver but absent on the sender,
# both named in --files-from.  Both must be deleted on the receiver.
#
# XFAIL until recv_file_entry() accepts the missing-args mode-0 entry again
# (the accompanying flist.c fix).  Runs at any uid.

import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    test_xfail, write_daemon_conf,
)

DAEMON_PORT = 12910

mod = SCRATCHDIR / 'recvmod910'    # daemon receive module
src = SCRATCHDIR / 'src910'
rmtree(mod)
rmtree(src)
makepath(mod / 'ghostdir', src)
(src / 'keep.txt').write_text("keep-me\n")            # present on sender
(mod / 'keep.txt').write_text("stale\n")              # will be updated
(mod / 'ghost.txt').write_text("delete-me-file\n")    # absent on sender -> delete
(mod / 'ghostdir' / 'inner').write_text("delete-me-dir\n")  # absent on sender -> delete

# --files-from lists one present file plus the two entries that are missing on
# the sender (a file and a directory) -- those become mode-0 "missing" entries.
flist = SCRATCHDIR / 'files910.lst'
flist.write_text("keep.txt\nghost.txt\nghostdir\n")

conf = write_daemon_conf([
    ('recv', {'path': str(mod), 'read only': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)

proc = subprocess.run(
    rsync_argv('-a', '--delete', '--delete-missing-args',
               f'--files-from={flist}', f'{src}/', f'{url}recv/'),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
out = proc.stdout or ''
print(out)

# Bug present: the receiver rejects the mode-0 missing-args entry.
if 'invalid file mode' in out or (proc.returncode == 2 and (mod / 'ghost.txt').exists()):
    test_xfail(
        "#910: --delete-missing-args with --files-from aborts with "
        "`invalid file mode 00 ... protocol incompatibility (code 2)`.  The "
        "sender sends mode-0 entries for the missing args (the delete signal), "
        "but recv_file_entry()'s 3.4.x mode-validation rejects mode 0 before the "
        "generator can delete them.  To be closed by accepting the "
        "missing-args mode-0 entry in recv_file_entry().")

# Bug fixed (or absent): both missing args were deleted, the present file kept.
if proc.returncode != 0:
    test_fail(f"transfer failed unexpectedly (rc={proc.returncode}); "
              f"not the #910 mode-00 symptom:\n{out}")
if (mod / 'ghost.txt').exists():
    test_fail("missing-arg file ghost.txt was not deleted on the receiver")
if (mod / 'ghostdir').exists():
    test_fail("missing-arg directory ghostdir was not deleted on the receiver")
if not (mod / 'keep.txt').is_file() or (mod / 'keep.txt').read_text() != "keep-me\n":
    test_fail("present file keep.txt was not delivered/updated correctly")
