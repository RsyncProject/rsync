#!/usr/bin/env python3
# --exclude-from / --include-from / --files-from / --filter=. file must not
# follow planted symlinks at attacker-controlled path components.
#
# Threat:
# privileged rsync reads from a planted symlink → file contents become
# filter rules / file list → silent transfer shaping, content leak via
# -v / -i / --debug output.
#
# Signal in this test (--exclude-from variant): the planted file's content
# is the basename of a "canary" file we placed in src. With the leak, rsync
# parses the planted content as an exclude rule and drops the canary from
# the transfer. With the fix, rsync errors on the planted symlink open
# (XFLG_FATAL_ERRORS at options.c:1543 + exclude.c:1477).
#
# Failure condition: returncode == 0 AND canary missing from dest. The only
# way that combination arises is if rsync read the planted symlink's target
# as filter rules. With the fix, rsync exits non-zero; with no leak at all,
# the canary transfers.
#
# 1.5 (-C $HOME/.cvsignore) is covered by the same fix code path
# (parse_filter_file callee shared with 1.1's per-dir merge); we don't
# duplicate that test here. The XFLG flag differs (1.5 is silent-skip,
# 1.4 is FATAL) but the open primitive is the same.
#
# Requires root for the cross-uid plant; skips otherwise.

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped


if os.geteuid() != 0:
    test_skipped("requires root to plant a symlink owned by a non-self uid "
                 "(the attacker simulation)")

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


base = SCRATCHDIR / 'excludefrom'
src = base / 'src'
dest = base / 'dest'
outside = base / 'outside'

rmtree(base)
src.mkdir(parents=True)
(src / 'canary_file').write_text("would-be-transferred\n")
(src / 'other_file').write_text("noise\n")
dest.mkdir(parents=True)
outside.mkdir(parents=True)

# Outside attacker-readable content: a CVS-style pattern that matches
# src/canary_file. If rsync follows the planted symlink and parses this as
# filter rules, it adds an exclude rule that drops canary_file.
(outside / 'exclude_list').write_text("canary_file\n")


def run_excludefrom(plant_path):
    rmtree(dest)
    dest.mkdir(parents=True)
    return subprocess.run(
        rsync_argv('-a', '--exclude-from=' + str(plant_path),
                   f'{src}/', f'{dest}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


plants = base / 'plants'
plants.mkdir()


# ---------------------------------------------------------------------------
# LEAF plant
# ---------------------------------------------------------------------------
leaf_plant = plants / 'exclude_leaf'
os.symlink(outside / 'exclude_list', leaf_plant)
os.lchown(leaf_plant, NOBODY_UID, NOBODY_UID)

leaf = run_excludefrom(leaf_plant)
canary = dest / 'canary_file'
if leaf.returncode == 0 and not canary.exists():
    test_fail(
        f"--exclude-from followed a planted LEAF symlink (owned by uid "
        f"{NOBODY_UID}): rsync read the symlink's target as filter rules and "
        f"applied the leaked pattern, dropping src/canary_file from the "
        f"transfer. Fix: refuse planted symlinks at --exclude-from.")


# ---------------------------------------------------------------------------
# PARENT-component plant
# ---------------------------------------------------------------------------
real_target = plants / 'parent_real_target'
real_target.mkdir()
(real_target / 'exclude_list').write_text("canary_file\n")

parent_plant = plants / 'parent_link'
os.symlink(real_target, parent_plant)
os.lchown(parent_plant, NOBODY_UID, NOBODY_UID)

parent = run_excludefrom(parent_plant / 'exclude_list')
canary = dest / 'canary_file'
if parent.returncode == 0 and not canary.exists():
    test_fail(
        f"--exclude-from followed a planted PARENT-component symlink (owned "
        f"by uid {NOBODY_UID}): {parent_plant} -> {real_target} was "
        f"traversed and the leaked exclude_list was applied, dropping "
        f"canary_file from the transfer. /tmp/somedir/-class parent-flip "
        "that plain O_NOFOLLOW does NOT defend. Fix: per-component path walk "
        "with parent-symlink ownership check.")
