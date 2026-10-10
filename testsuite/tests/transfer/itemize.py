#!/usr/bin/env python3

import os
import shutil

from harness.rsync import (
    FROMDIR,
    RSYNC,
    SCRATCHDIR,
    SRCDIR,
    TMPDIR,
    TODIR,
    all_plus,
    allspace,
    dots,
    checkdiff,
    cp_p,
    makepath,
    run_rsync,
    v_filt,
    hardlink_symlinks_supported,
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

vv = run_rsync('-VV', check=True, capture_output=True).stdout
hardlink_symlinks = '"hardlink_symlinks": true' in vv
symtimes_supported = '"symtimes": true' in vv
L = ('hL' if hardlink_symlinks and hardlink_symlinks_supported(SCRATCHDIR)
     else 'cL')

if 'protocol=2' in RSYNC:
    T = '.T'
elif symtimes_supported:
    T = '.t'
else:
    T = '.T'

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

run_rsync('-a', '-f', '-! */', f'{FROMDIR}/', str(TODIR))

cp_p(SRCDIR / 'configure.ac', FROMDIR / 'foo' / 'config2')
os.chmod(FROMDIR / 'foo' / 'config2', 0o601)

checkdiff(['-iplrH', f'{FROMDIR}/', f'{TODIR}/'],
          f">f..T.{dots} bar/baz/rsync\n"
          f">f..T.{dots} foo/config1\n"
          f">f.sTp{dots} foo/config2\n"
          f"hf..T.{dots} foo/extra => foo/config1\n")

run_rsync('-a', '-f', '-! */', f'{FROMDIR}/', str(TODIR))
cp_p(SRCDIR / 'config.sub', FROMDIR / 'foo' / 'config2')
(TODIR / 'foo' / 'sym').unlink()
old_umask = os.umask(0)
try:
    os.symlink('../bar/baz', TODIR / 'foo' / 'sym')
finally:
    os.umask(old_umask)
src_foo = (FROMDIR / 'foo').stat()
os.utime(TODIR / 'foo', (src_foo.st_atime, src_foo.st_mtime + 10))
if symtimes_supported:
    src_sym = os.stat(FROMDIR / 'foo' / 'sym', follow_symlinks=False)
    os.utime(TODIR / 'foo' / 'sym',
             (src_sym.st_atime, src_sym.st_mtime + 10),
             follow_symlinks=False)
os.chmod(FROMDIR / 'foo' / 'config2', 0o600)
os.chmod(TODIR / 'bar' / 'baz' / 'rsync', 0o777)

checkdiff(['-iplrtc', f'{FROMDIR}/', f'{TODIR}/'],
          f".f..tp{dots} bar/baz/rsync\n"
          f".d..t.{dots} foo/\n"
          f".f..t.{dots} foo/config1\n"
          f">fcstp{dots} foo/config2\n"
          f"cLc{T}.{dots} foo/sym -> ../bar/baz/rsync\n")

cp_p(SRCDIR / 'configure.ac', FROMDIR / 'foo' / 'config2')
os.chmod(FROMDIR / 'foo' / 'config2', 0o600)

checkdiff(['-vvplrH', f'{FROMDIR}/', f'{TODIR}/'],
          "bar/baz/rsync is uptodate\n"
          "foo/config1 is uptodate\n"
          "foo/extra is uptodate\n"
          "foo/sym is uptodate\n"
          "foo/config2\n",
          filter=v_filt)

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

os.chmod(TODIR / 'foo' / 'config1', 0o757)
src_config2 = (FROMDIR / 'foo' / 'config2').stat()
os.utime(TODIR / 'foo' / 'config2',
         (src_config2.st_atime, src_config2.st_mtime + 10))
checkdiff(['-vplrtH', f'{FROMDIR}/', f'{TODIR}/'],
          "foo/config2\n",
          filter=v_filt)

os.chmod(TODIR / 'foo' / 'config1', 0o757)
src_config2 = (FROMDIR / 'foo' / 'config2').stat()
os.utime(TODIR / 'foo' / 'config2',
         (src_config2.st_atime, src_config2.st_mtime + 10))
checkdiff(['-iplrtH', f'{FROMDIR}/', f'{TODIR}/'],
          f".f...p{dots} foo/config1\n"
          f">f..t.{dots} foo/config2\n")

checkdiff(['-ivvplrtH', '--copy-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"cd{allspace} ./\n"
          f"cd{allspace} bar/\n"
          f"cd{allspace} bar/baz/\n"
          f"cf{allspace} bar/baz/rsync\n"
          f"cd{allspace} foo/\n"
          f"cf{allspace} foo/config1\n"
          f"cf{allspace} foo/config2\n"
          f"hf{allspace} foo/extra => foo/config1\n"
          f"cL{allspace} foo/sym -> ../bar/baz/rsync\n",
          filter=v_filt)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', '--copy-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n"
          f"hf{allspace} foo/extra => foo/config1\n")

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-vvplrtH', f'--copy-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          "./ is uptodate\n"
          "bar/ is uptodate\n"
          "bar/baz/ is uptodate\n"
          "bar/baz/rsync is uptodate\n"
          "foo/ is uptodate\n"
          "foo/config1 is uptodate\n"
          "foo/config2 is uptodate\n"
          f"foo/sym is uptodate\n"
          "foo/extra => foo/config1\n",
          filter=v_filt)

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
          f"{L}{allspace} foo/sym -> ../bar/baz/rsync\n",
          filter=v_filt)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', '--dry-run', '--link-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n")

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', '--link-dest=../to', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n")

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
          f"foo/sym is uptodate\n",
          filter=v_filt)

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
          f".L{allspace} foo/sym -> ../bar/baz/rsync\n",
          filter=v_filt)

shutil.rmtree(to2dir, ignore_errors=True)
checkdiff(['-iplrtH', f'--compare-dest={TODIR}', f'{FROMDIR}/', f'{to2dir}/'],
          f"created directory {to2dir}\n")

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
          f"foo/sym is uptodate\n",
          filter=v_filt)
