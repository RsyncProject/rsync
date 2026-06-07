#!/usr/bin/env python3
# Python rewrite of testsuite/itemize.test.
#
# Test the output of various copy commands to ensure itemized output
# (-i, -ii) and double-verbose output (-vv) match the canonical
# representations across whole-file and delta paths.

import os
import shutil

from rsyncfns import (
    CHKFILE, FROMDIR, RSYNC, SCRATCHDIR, SRCDIR, TMPDIR, TODIR,
    all_plus, allspace, dots,
    checkdiff, cp_p, makepath, run_rsync, v_filt,
)


to2dir = TMPDIR / 'to2'

makepath(FROMDIR / 'foo', FROMDIR / 'bar' / 'baz')
cp_p(SRCDIR / 'configure.ac', FROMDIR / 'foo' / 'config1')
cp_p(SRCDIR / 'config.sub', FROMDIR / 'foo' / 'config2')
cp_p(SRCDIR / 'rsync.h', FROMDIR / 'bar' / 'baz' / 'rsync')
os.chmod(FROMDIR / 'foo' / 'config1', 0o600)
os.chmod(FROMDIR / 'foo' / 'config2', 0o600)
os.chmod(FROMDIR / 'bar' / 'baz' / 'rsync', 0o600)

old_umask = os.umask(0)
try:
    os.symlink('../bar/baz/rsync', FROMDIR / 'foo' / 'sym')
finally:
    os.umask(old_umask)

os.link(FROMDIR / 'foo' / 'config1', FROMDIR / 'foo' / 'extra')
if to2dir.is_file():
    to2dir.unlink()


# Detect what this rsync build supports.
vv = run_rsync('-VV', check=True, capture_output=True).stdout
hardlink_symlinks = '"hardlink_symlinks": true' in vv
symtimes_supported = '"symtimes": true' in vv

if hardlink_symlinks:
    L = 'hL'
    sym_dots = allspace
    L_sym_dots = '.L' + allspace
    is_uptodate = 'is uptodate'
    chkfile_extra = ''  # no extra trailing line
else:
    L = 'cL'
    sym_dots = 'c.t.' + dots
    L_sym_dots = 'cL' + sym_dots
    is_uptodate = '-> ../bar/baz/rsync'
    chkfile_extra = f"cL{sym_dots} foo/sym {is_uptodate}\n"

if 'protocol=2' in RSYNC:
    T = '.T'
elif symtimes_supported:
    T = '.t'
else:
    T = '.T'


# First check: -iplr basic itemize on a fresh transfer.
checkdiff(['-iplr', f'{FROMDIR}/', f'{TODIR}/'],
          f"created directory {TODIR}\n"
          f"cd{all_plus} ./\n"
          f"cd{all_plus} bar/\n"
          f"cd{all_plus} bar/baz/\n"
          f">f{all_plus} bar/baz/rsync\n"
          f"cd{all_plus} foo/\n"
          f">f{all_plus} foo/config1\n"
          f">f{all_plus} foo/config2\n"
          f">f{all_plus} foo/extra\n"
          f"cL{all_plus} foo/sym -> ../bar/baz/rsync\n")

# Touch dir times so subsequent itemize diffs don't pick up dir-time noise.
run_rsync('-a', '-f', '-! */', f'{FROMDIR}/', str(TODIR))

# Permute one file's content + mode; expect a content/mode itemize.
cp_p(SRCDIR / 'configure.ac', FROMDIR / 'foo' / 'config2')
os.chmod(FROMDIR / 'foo' / 'config2', 0o601)

checkdiff(['-iplrH', f'{FROMDIR}/', f'{TODIR}/'],
          f">f..T.{dots} bar/baz/rsync\n"
          f">f..T.{dots} foo/config1\n"
          f">f.sTp{dots} foo/config2\n"
          f"hf..T.{dots} foo/extra => foo/config1\n")

# Re-touch dirs, permute config2 again and replace the symlink target.
run_rsync('-a', '-f', '-! */', f'{FROMDIR}/', str(TODIR))
cp_p(SRCDIR / 'config.sub', FROMDIR / 'foo' / 'config2')
import time
time.sleep(1)  # to provoke a directory mtime change below
(TODIR / 'foo' / 'sym').unlink()
old_umask = os.umask(0)
try:
    os.symlink('../bar/baz', TODIR / 'foo' / 'sym')
finally:
    os.umask(old_umask)
os.chmod(FROMDIR / 'foo' / 'config2', 0o600)
os.chmod(TODIR / 'bar' / 'baz' / 'rsync', 0o777)

checkdiff(['-iplrtc', f'{FROMDIR}/', f'{TODIR}/'],
          f".f..tp{dots} bar/baz/rsync\n"
          f".d..t.{dots} foo/\n"
          f".f..t.{dots} foo/config1\n"
          f">fcstp{dots} foo/config2\n"
          f"cLc{T}.{dots} foo/sym -> ../bar/baz/rsync\n")

# Re-permute config2, leaving the others untouched; lack of -t is for
# the unchanged-hard-link stress test.
cp_p(SRCDIR / 'configure.ac', FROMDIR / 'foo' / 'config2')
os.chmod(FROMDIR / 'foo' / 'config2', 0o600)

checkdiff(['-vvplrH', f'{FROMDIR}/', f'{TODIR}/'],
          "bar/baz/rsync is uptodate\n"
          "foo/config1 is uptodate\n"
          "foo/extra is uptodate\n"
          "foo/sym is uptodate\n"
          "foo/config2\n",
          filter=v_filt)

# Touch a mode change on one dest file then run -ii to see "no change".
os.chmod(TODIR / 'bar' / 'baz' / 'rsync', 0o747)
run_rsync('-a', '-f', '-! */', f'{FROMDIR}/', str(TODIR))

checkdiff(['-ivvplrtH', f'{FROMDIR}/', f'{TODIR}/'],
          f".d{allspace} ./\n"
          f".d{allspace} bar/\n"
          f".d{allspace} bar/baz/\n"
          f".f...p{dots} bar/baz/rsync\n"
          f".d{allspace} foo/\n"
          f".f{allspace} foo/config1\n"
          f">f..t.{dots} foo/config2\n"
          f"hf{allspace} foo/extra\n"
          f".L{allspace} foo/sym -> ../bar/baz/rsync\n",
          filter=v_filt)

# Permute one perm and re-touch a file; expect just those two itemizes.
os.chmod(TODIR / 'foo' / 'config1', 0o757)
(TODIR / 'foo' / 'config2').touch()
checkdiff(['-vplrtH', f'{FROMDIR}/', f'{TODIR}/'],
          "foo/config2\n",
          filter=v_filt)

os.chmod(TODIR / 'foo' / 'config1', 0o757)
(TODIR / 'foo' / 'config2').touch()
checkdiff(['-iplrtH', f'{FROMDIR}/', f'{TODIR}/'],
          f".f...p{dots} foo/config1\n"
          f">f..t.{dots} foo/config2\n")


# --copy-dest variants.
checkdiff(['-ivvplrtH', '--copy-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"cd{allspace} ./\n"
          f"cd{allspace} bar/\n"
          f"cd{allspace} bar/baz/\n"
          f"cf{allspace} bar/baz/rsync\n"
          f"cd{allspace} foo/\n"
          f"cf{allspace} foo/config1\n"
          f"cf{allspace} foo/config2\n"
          f"hf{allspace} foo/extra => foo/config1\n"
          f"cL{sym_dots} foo/sym -> ../bar/baz/rsync\n",
          filter=v_filt)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', '--copy-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n"
          f"hf{allspace} foo/extra => foo/config1\n"
          + chkfile_extra)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-vvplrtH', f'--copy-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          "./ is uptodate\n"
          "bar/ is uptodate\n"
          "bar/baz/ is uptodate\n"
          "bar/baz/rsync is uptodate\n"
          "foo/ is uptodate\n"
          "foo/config1 is uptodate\n"
          "foo/config2 is uptodate\n"
          f"foo/sym {is_uptodate}\n"
          "foo/extra => foo/config1\n",
          filter=v_filt)


# --link-dest variants.
shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-ivvplrtH', f'--link-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          f"cd{allspace} ./\n"
          f"cd{allspace} bar/\n"
          f"cd{allspace} bar/baz/\n"
          f"hf{allspace} bar/baz/rsync\n"
          f"cd{allspace} foo/\n"
          f"hf{allspace} foo/config1\n"
          f"hf{allspace} foo/config2\n"
          f"hf{allspace} foo/extra => foo/config1\n"
          f"{L}{sym_dots} foo/sym -> ../bar/baz/rsync\n",
          filter=v_filt)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', '--dry-run', '--link-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n"
          + chkfile_extra)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', '--link-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n"
          + chkfile_extra)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-vvplrtH', f'--link-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          "./ is uptodate\n"
          "bar/ is uptodate\n"
          "bar/baz/ is uptodate\n"
          "bar/baz/rsync is uptodate\n"
          "foo/ is uptodate\n"
          "foo/config1 is uptodate\n"
          "foo/config2 is uptodate\n"
          "foo/extra is uptodate\n"
          f"foo/sym {is_uptodate}\n",
          filter=v_filt)


# --compare-dest variants.
shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-ivvplrtH', f'--compare-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          f"cd{allspace} ./\n"
          f"cd{allspace} bar/\n"
          f"cd{allspace} bar/baz/\n"
          f".f{allspace} bar/baz/rsync\n"
          f"cd{allspace} foo/\n"
          f".f{allspace} foo/config1\n"
          f".f{allspace} foo/config2\n"
          f".f{allspace} foo/extra\n"
          f"{L_sym_dots} foo/sym -> ../bar/baz/rsync\n",
          filter=v_filt)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', f'--compare-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n"
          + chkfile_extra)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-vvplrtH', f'--compare-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          "./ is uptodate\n"
          "bar/ is uptodate\n"
          "bar/baz/ is uptodate\n"
          "bar/baz/rsync is uptodate\n"
          "foo/ is uptodate\n"
          "foo/config1 is uptodate\n"
          "foo/config2 is uptodate\n"
          "foo/extra is uptodate\n"
          f"foo/sym {is_uptodate}\n",
          filter=v_filt)
