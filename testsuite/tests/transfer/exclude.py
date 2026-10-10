#!/usr/bin/env python3

import os
import shutil
import subprocess
import sys
import time

from harness.rsync import (
    CHKDIR,
    FROMDIR,
    SCRATCHDIR,
    TODIR,
    all_plus,
    allspace,
    dots,
    cp_touch,
    checkdiff,
    checkit,
    makepath,
    rsync_argv,
    run_rsync,
    test_fail,
    verify_dirs,
    rsync_path_arg,
    rsh_cmd,
)
from harness import metadata

metadata(features={'filters', 'remote-shell'}, transports={'pipe', 'tcp'}, min_peer='2.6.0', mutates={'environment', 'filesystem', 'process'}, tags={'compatibility', 'transfer', 'version-mix'})

os.environ['CVSIGNORE'] = '*.junk'

script_name = os.path.basename(sys.argv[0] if sys.argv[0] else __file__)
if 'lsh' in script_name:
    os.environ['RSYNC_RSH'] = rsh_cmd()
    rpath = [f'--rsync-path={rsync_path_arg()}']
    host = 'lh:'
else:
    rpath = []
    host = ''

makepath(
    FROMDIR / 'foo/down/to/you',
    FROMDIR / 'foo/sub',
    FROMDIR / 'bar/down/to/foo/too',
    FROMDIR / 'bar/down/to/bar/baz',
    FROMDIR / 'mid/for/foo/and/that/is/who',
    FROMDIR / 'new/keep/this',
    FROMDIR / 'new/lose/this',
)

(FROMDIR / '.filt').write_text(
    "exclude down\n"
    ": .filt-temp\n"
    "clear\n"
    "- .filt\n"
    "- *.bak\n"
    "- *.old\n"
)
(FROMDIR / 'foo' / 'file1').write_text("filtered-1\n")
(FROMDIR / 'foo' / 'file2').write_text("removed\n")
(FROMDIR / 'foo' / 'file2.old').write_text("cvsout\n")
(FROMDIR / 'foo' / '.filt').write_text("include .filt\n- /file1\n")
(FROMDIR / 'foo' / 'sub' / 'file1').write_text("not-filtered-1\n")

(FROMDIR / 'bar' / '.filt').write_text(
    "- home-cvs-exclude\n"
    "dir-merge .filt2\n"
    "+ to\n"
)
(FROMDIR / 'bar' / 'down' / 'to' / 'home-cvs-exclude').write_text("cvsout\n")
(FROMDIR / 'bar' / 'down' / 'to' / '.filt2').write_text("- .filt2\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / '.filt2').write_text("+ *.junk\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'file1').write_text("keeper\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'file1.bak').write_text("cvsout\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'file3').write_text("gone\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'file4').write_text("lost\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / '+ file3').write_text("weird\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'file4.junk').write_text("cvsout-but-filtin\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'to').write_text("smashed\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'bar' / '.filt2').write_text("- *.deep\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'bar' / 'baz' / 'file5.deep').write_text("filtout\n")

(FROMDIR / 'mid' / '.filt2').write_text("- extra\n")
(FROMDIR / 'mid' / 'one-in-one-out').write_text("cvsout\n")
(FROMDIR / 'mid' / '.cvsignore').write_text("one-in-one-out\n")
(FROMDIR / 'mid' / 'one-for-all').write_text("cvsin\n")
(FROMDIR / 'mid' / '.filt').write_text(":C\n")
(FROMDIR / 'mid' / 'for' / 'one-in-one-out').write_text("cvsin\n")
(FROMDIR / 'mid' / 'for' / 'foo' / 'extra').write_text("expunged\n")
(FROMDIR / 'mid' / 'for' / 'foo' / 'keep').write_text("retained\n")

excl = SCRATCHDIR / 'exclude-from'
excl.write_text(
    "!\n"
    "# If the second line of these two lines does anything, it's a bug.\n"
    "+ **/bar\n"
    "- /bar\n"
    "# This should match against the whole path, not just the name.\n"
    "+ foo**too\n"
    "# These should float at the end of the path.\n"
    "+ foo/s?b/\n"
    "- foo/*/\n"
    "# Test how /** differs from /***\n"
    "- new/keep/**\n"
    "- new/lose/***\n"
    "# Test some normal excludes. Competing lines are paired.\n"
    "+ t[o]/\n"
    "- to\n"
    "+ file4\n"
    "- file[2-9]\n"
    "- /mid/for/foo/extra\n"
)

(SCRATCHDIR / '.cvsignore').write_text("home-cvs-exclude\n")

run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '-f', '-_foo/too/', '-f', '-_foo/down/',
          '-f', '-_foo/and/', '-f', '-_new/',
          f'{host}{FROMDIR}/', f'{CHKDIR}/')

checkit(['-av', *rpath, '--prune-empty-dirs',
         f'{host}{FROMDIR}/', f'{TODIR}/'], CHKDIR, TODIR)

shutil.rmtree(TODIR, ignore_errors=True)

os.symlink('too', FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'sym')

up1 = SCRATCHDIR / 'up1'
up2 = SCRATCHDIR / 'up2'
up1.mkdir()
up2.mkdir()
(up1 / 'dst-newness').touch()
(up2 / 'src-newness').touch()
(up1 / 'same-newness').touch()
(up2 / 'same-newness').touch()
(up1 / 'extra-src').touch()
(up2 / 'extra-dest').touch()

checkit(['-avv', *rpath, f'{host}{FROMDIR}/', f'{CHKDIR}/'], FROMDIR, CHKDIR)
time.sleep(1)
shutil.rmtree(CHKDIR / 'foo' / 'down', ignore_errors=True)
shutil.rmtree(CHKDIR / 'mid' / 'for' / 'foo' / 'and', ignore_errors=True)
shutil.rmtree(CHKDIR / 'new' / 'keep' / 'this', ignore_errors=True)
shutil.rmtree(CHKDIR / 'new' / 'lose', ignore_errors=True)
for f in (CHKDIR / 'foo').glob('file[235-9]'):
    f.unlink()
(CHKDIR / 'bar' / 'down' / 'to' / 'foo' / 'to').unlink()
for f in (CHKDIR / 'bar' / 'down' / 'to' / 'foo').glob('file[235-9]'):
    f.unlink()
(CHKDIR / 'mid' / 'for' / 'foo' / 'extra').unlink()

(up1 / 'src-newness').touch()
(up2 / 'dst-newness').touch()

run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--existing', '--include=*/', '--exclude=*',
          f'{host}{FROMDIR}/', f'{CHKDIR}/')

checkit(['-avv', *rpath, f'--exclude-from={excl}',
         '--delete-during', f'{host}{FROMDIR}/', f'{TODIR}/'],
        CHKDIR, TODIR)

for f in (CHKDIR / 'foo').glob('*.old'):
    f.unlink()
for f in (CHKDIR / 'bar' / 'down' / 'to' / 'foo').glob('*.bak'):
    f.unlink()
for f in (CHKDIR / 'bar' / 'down' / 'to' / 'foo').glob('*.junk'):
    f.unlink()
(CHKDIR / 'bar' / 'down' / 'to' / 'home-cvs-exclude').unlink()
(CHKDIR / 'mid' / 'one-in-one-out').unlink()

run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--existing', '--filter=exclude,! */',
          f'{host}{FROMDIR}/', f'{CHKDIR}/')

checkit(['-avv', *rpath, f'--filter=merge {excl}',
         '-f:C', '-f-C', '--delete-excluded', '--delete-during',
         f'{host}{FROMDIR}/', f'{TODIR}/'],
        CHKDIR, TODIR)

(CHKDIR / 'foo' / 'file1').unlink()
for f in (CHKDIR / 'bar' / 'down' / 'to' / 'bar' / 'baz').glob('*.deep'):
    f.unlink()
for src in (FROMDIR / 'bar' / 'down' / 'to' / 'foo').glob('*.junk'):
    cp_touch_dst = CHKDIR / 'bar' / 'down' / 'to' / 'foo'
    cp_touch_dst.mkdir(exist_ok=True)
    cp_touch(src, cp_touch_dst)
cp_touch(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / 'to',
         CHKDIR / 'bar' / 'down' / 'to' / 'foo')

run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--existing', '-f', 'show .filt*', '-f', 'hide,! */', '--del',
          f'{host}{FROMDIR}/', f'{TODIR}/')

(TODIR / 'bar' / 'down' / 'to' / 'bar' / 'baz' / 'nodel.deep').write_text("retained\n")
cp_touch(TODIR / 'bar' / 'down' / 'to' / 'bar' / 'baz' / 'nodel.deep',
         CHKDIR / 'bar' / 'down' / 'to' / 'bar' / 'baz')

run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--existing', '--filter=-! */',
          f'{host}{FROMDIR}/', f'{CHKDIR}/')

filtered_excl = SCRATCHDIR / 'exclude-from-filtered'
filtered_excl.write_text(
    '\n'.join(ln for ln in excl.read_text().splitlines() if '!' not in ln)
    + '\n'
)

def run_with_stdin_filter(args, label="merge"):
    print(f"Running: rsync {' '.join(args)}")
    with open(filtered_excl, 'rb') as inp:
        proc = subprocess.run(rsync_argv(*args), stdin=inp)
    if proc.returncode != 0:
        test_fail(f"{label}: rsync exited {proc.returncode}")

run_with_stdin_filter(
    ['-avv', *rpath, '-f', 'dir-merge_.filt', '-f', 'merge_-',
     '--delete-during', f'{host}{FROMDIR}/', f'{TODIR}/'],
    "dir-merge .filt + merge from stdin",
)
verify_dirs(CHKDIR, TODIR, label="dir-merge + merge-from-stdin")

(CHKDIR / '.filt').unlink()
(CHKDIR / 'bar' / '.filt').unlink()
(CHKDIR / 'bar' / 'down' / 'to' / '.filt2').unlink()
(CHKDIR / 'bar' / 'down' / 'to' / 'foo' / '.filt2').unlink()
(CHKDIR / 'bar' / 'down' / 'to' / 'bar' / '.filt2').unlink()
(CHKDIR / 'mid' / '.filt').unlink()

run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--existing', '--include=*/', '--exclude=*',
          f'{host}{FROMDIR}/', f'{CHKDIR}/')

run_with_stdin_filter(
    ['-avv', *rpath, '-f', ':s_.filt', '-f', '.s_-',
     '-f', 'P_nodel.deep',
     '--delete-before', f'{host}{FROMDIR}/', f'{TODIR}/'],
    "delete-before with merge",
)
verify_dirs(CHKDIR, TODIR, label="delete-before with merge")

(FROMDIR / 'bar' / 'down' / '.excl').write_text("file3\n")
(FROMDIR / 'bar' / 'down' / 'to' / 'foo' / '.excl').write_text(
    "+ file3\n*.bak\n"
)

run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--del', f'{host}{FROMDIR}/', f'{CHKDIR}/')
(CHKDIR / 'bar' / 'down' / 'to' / 'foo' / 'file1.bak').unlink()
(CHKDIR / 'bar' / 'down' / 'to' / 'foo' / 'file3').unlink()
(CHKDIR / 'bar' / 'down' / 'to' / 'foo' / '+ file3').unlink()
run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--existing', '--filter=-! */',
          f'{host}{FROMDIR}/', f'{CHKDIR}/')
run_rsync('-av', f'--rsync-path={rsync_path_arg()}',
          '--delete-excluded', '--exclude=*',
          f'{host}{FROMDIR}/', f'{TODIR}/')

checkit(['-avv', *rpath, '-f', 'dir-merge,-_.excl',
         f'{host}{FROMDIR}/', f'{TODIR}/'], CHKDIR, TODIR)

relative_opts = ['--relative', '--chmod=Du+w', '--copy-unsafe-links']
run_rsync('-av', f'--rsync-path={rsync_path_arg()}', *relative_opts,
          f'{host}{FROMDIR}/foo', f'{CHKDIR}/')
shutil.rmtree(str(CHKDIR) + str(FROMDIR) + '/foo/down', ignore_errors=True)
run_rsync('-av', *relative_opts, '--existing', '--filter=-! */',
          f'{FROMDIR}/foo', f'{CHKDIR}/')

checkit(['-avv', *rpath, *relative_opts,
         f'--exclude={FROMDIR}/foo/down',
         f'{host}{FROMDIR}/foo', str(TODIR)],
        str(CHKDIR) + str(FROMDIR) + '/foo',
        str(TODIR) + str(FROMDIR) + '/foo')

checkdiff(
    ['-aiiO', *rpath, '--update', '--info=skip',
     f'{host}{SCRATCHDIR}/up1/', f'{SCRATCHDIR}/up2/'],
    "dst-newness is newer\n"
    f">f{all_plus} extra-src\n"
    f".f{allspace} same-newness\n"
    f">f..t.{dots} src-newness\n",
    filter=lambda txt: '\n'.join(
        ln for ln in txt.splitlines()
        if not ln.startswith('.d' + allspace)
    ) + ('\n' if txt else ''),
)
