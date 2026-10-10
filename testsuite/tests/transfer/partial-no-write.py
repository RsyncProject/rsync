#!/usr/bin/env python3

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from harness.rsync import make_data_file, cp_p, makepath, checkit, RSYNC, TMPDIR, get_testuid, get_rootuid

BASEDIR = TMPDIR

FROMDIR = BASEDIR / 'from'
TODIR = BASEDIR / 'to'

makepath(FROMDIR)
makepath(TODIR)

makepath(FROMDIR)
make_data_file(FROMDIR / 'some_file', 1 * 1024 * 1024)
os.chmod(FROMDIR / 'some_file', 0o444)

makepath(TODIR / '.~tmp~')
os.chmod(TODIR / '.~tmp~', 0o700)
cp_p(FROMDIR / 'some_file', TODIR / '.~tmp~' / 'some_file')

is_root = get_testuid() == get_rootuid()

_cwd_st = os.stat(os.getcwd())
_cwd_traversable = ((_cwd_st.st_uid == 0 and _cwd_st.st_mode & 0o100)
                    or _cwd_st.st_mode & 0o001)
if (is_root and sys.platform == 'linux' and hasattr(os, 'unshare')
        and shutil.which('setpriv') and _cwd_traversable):
    try:
        cwd = Path(os.getcwd())
        chown_target = None
        for p in reversed(cwd.parents):
            st = p.stat()
            if not (st.st_uid == 0 or st.st_mode & 0o005):
                chown_target = p
                break
        if chown_target is not None:
            os.unshare(os.CLONE_NEWNS)
            subprocess.run(['mount', '--make-rprivate', '/'], check=True)
            tempdir = tempfile.mkdtemp()
            subprocess.run(['mount', '--bind', cwd, tempdir], check=True)
            subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=0755', 'tmpfs', chown_target], check=True)
            makepath(cwd)
            subprocess.run(['mount', '--bind', tempdir, cwd], check=True)
            subprocess.run(['umount', tempdir], check=True)
            os.rmdir(tempdir)
        from harness import rsync
        rsync.RSYNC = "setpriv --inh-caps -all --bounding-set -all " + RSYNC
    except (OSError, subprocess.CalledProcessError):
        pass

checkit(['-avv', '--partial', '--delay-updates', f'{FROMDIR}/', f'{TODIR}/'], FROMDIR, TODIR)
