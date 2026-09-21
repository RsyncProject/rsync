#!/usr/bin/env python3
# Per-directory filter merge files (.cvsignore under -C, dir-merge rules)
# must not follow a planted symlink at an attacker-controlled path.
#
# Threat (the "root runs `rsync -a /home /backup`" disclosure class):
# during a recursive scan the sender opens a per-directory filter file from
# EACH source directory (exclude.c:1464 fopen via parse_filter_file, called
# from push_local_filters at exclude.c:811-814). The path is composed as
# "<scanned source dir>/<merge pattern>", so an unprivileged user who
# controls a source directory can plant `.cvsignore` (or whatever the
# dir-merge filename is) as a symlink to ANY file the privileged rsync can
# read. rsync then parses that file's bytes as filter rules -- with two
# observable effects:
#
#   (a) Content shaping. Lines of the planted-target file become filter
#       patterns, which affect what does or doesn't transfer; the operator
#       cannot easily detect this. (Demonstrated below.)
#   (b) Direct disclosure under --debug=FILTER. The parsed rules are
#       printed verbatim. (Not exercised here; demonstrated by manual
#       inspection.)
#
# The fix refuses symlinks anywhere in the path unless owned by uid 0 or
# our effective uid (the trusted set).  Cross-uid setup is the
# realistic threat shape (privileged user runs rsync over an unprivileged
# user's tree): the runner is in the trusted set, the planted symlink is
# not, so the fix refuses it; parse_filter_file silently skips the file
# (XFLG_ANCHORED2ABS, not XFLG_FATAL_ERRORS), no exclude is added, and
# the canary transfers normally.
#
# Requires root to plant a symlink owned by a non-self uid (the attacker
# simulation); skips otherwise -- without cross-uid the runner trusts
# their own symlinks and the fix has nothing to refuse.

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped


if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(simulates the 'root runs `rsync -a /home /backup`' case)")

NOBODY_UID = None
for name in ('nobody', 'nfsnobody', 'daemon'):
    try:
        u = pwd.getpwnam(name).pw_uid
        if u != 0 and u != os.geteuid():
            NOBODY_UID = u
            break
    except KeyError:
        continue
if NOBODY_UID is None:
    test_skipped("no untrusted-uid user available for cross-uid plant")


base = SCRATCHDIR / 'filtermerge'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'

rmtree(base)
(src / 'sub').mkdir(parents=True)
# A canary the test will look for in the destination. It will be excluded by
# rsync IFF a planted .cvsignore symlink is followed and its content (the
# pattern below) is applied as a filter rule.
(src / 'sub' / 'canary_file').write_text("would-be-transferred\n")

# The attacker-readable "secret" outside the source tree. Its contents are a
# single CVS-style pattern that exactly matches the canary's basename.
outside.mkdir(parents=True)
(outside / 'attacker_target').write_text("canary_file\n")

# Plant: src/sub/.cvsignore is a symlink to the outside file, OWNED BY THE
# ATTACKER (NOBODY_UID). With -C, rsync normally reads .cvsignore as filter
# rules per directory; a privileged rsync over an unprivileged-user-controlled
# source thus reads the symlink's target unless defended.
plant = src / 'sub' / '.cvsignore'
os.symlink('../../outside/attacker_target', plant)
os.lchown(plant, NOBODY_UID, NOBODY_UID)

dest.mkdir(parents=True)

# -aC -- preserve everything + cvs-exclude (reads .cvsignore per dir).
# The transfer itself succeeds either way; the question is whether the planted
# .cvsignore was followed and its content applied as filter rules.
subprocess.run(
    rsync_argv('-aC', f'{src}/', f'{dest}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if not (dest / 'sub' / 'canary_file').exists():
    test_fail(
        f"per-dir merge file .cvsignore (planted as a symlink owned by uid "
        f"{NOBODY_UID}) was followed: rsync read the symlink's target outside "
        f"the source tree and applied its content as a filter rule, dropping "
        f"src/sub/canary_file from the transfer. The operator-controlled "
        f"path was bypassed via an unprivileged-user-planted symlink. Fix: "
        f"refuse symlinks owned by untrusted uids at the per-dir merge open."
    )
