import os
import platform
import sys
from pathlib import Path
from .results import Exit

def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(
            f"test harness: required environment variable {name} is not set; "
            "run this test via runtests.py rather than directly.\n"
        )
        sys.exit(Exit.ERROR)
    return v

SCRATCHDIR = Path(_required('scratchdir'))
SRCDIR = Path(_required('srcdir'))
TOOLDIR = Path(_required('TOOLDIR'))
SUITEDIR = Path(os.environ.get('suitedir', SRCDIR / 'testsuite'))

os.umask(0o022)
os.environ['HOME'] = str(SCRATCHDIR)

RSYNC = _required('RSYNC')
RSYNC_PEER = os.environ.get('RSYNC_PEER', RSYNC)
TLS_ARGS = os.environ.get('TLS_ARGS', '')

all_plus = '+++++++++'
allspace = '         '
dots = '.....'

TMPDIR = SCRATCHDIR
FROMDIR = SCRATCHDIR / 'from'
TODIR = SCRATCHDIR / 'to'
CHKDIR = SCRATCHDIR / 'chk'
CHKFILE = SCRATCHDIR / 'rsync.chk'
OUTFILE = SCRATCHDIR / 'rsync.out'

_SYSTEM = platform.system()
_CYGWIN = _SYSTEM.startswith('CYGWIN')
