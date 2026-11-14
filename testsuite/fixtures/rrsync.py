import os
from pathlib import Path
import shutil
import sys

from harness import environment
from harness.process import expect_fail
from harness.rsync import rsync_argv
from harness.ssh import write_rrsync

def rsync_wrapper(path, *options):
    command = rsync_argv(*options)
    path.write_text(f'#!{sys.executable}\nimport os, sys\ncommand = {command!r}\n'
                    'os.execvp(command[0], [*command, *sys.argv[1:]])\n')
    path.chmod(0o755)

def rrsync_shell(path, wrapper, restricted, *options):
    command = [str(wrapper), *options, str(restricted)]
    path.write_text(f'#!{sys.executable}\nimport os, sys\ncommand = {command!r}\n'
                    'os.environ["SSH_ORIGINAL_COMMAND"] = " ".join(sys.argv[2:])\n'
                    'os.execv(command[0], command)\n')
    path.chmod(0o755)

def patched_rrsync(workdir, rsync_path=None):
    if rsync_path is None:
        rsync_path = shutil.which('true') or '/usr/bin/true'
    src = environment.SRCDIR / 'support' / 'rrsync'
    dst = Path(workdir) / 'rrsync-under-test'
    return write_rrsync(src, dst, rsync_path)

def run_rrsync_denied(command, expected):
    base = environment.SCRATCHDIR / expected.replace(' ', '_').replace('/', '_')
    base.mkdir(parents=True, exist_ok=True)
    restricted = base / 'restricted'
    restricted.mkdir(exist_ok=True)
    rrsync = patched_rrsync(base)
    env = {**os.environ, 'SSH_ORIGINAL_COMMAND': command}
    expect_fail([str(rrsync), '-ro', '-no-lock', str(restricted)], expected, env=env)
