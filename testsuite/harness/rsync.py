import subprocess
from . import environment
from .process import (
    forced_protocol as _forced_protocol, rsh_cmd as _rsh_cmd, rsync_argv as _rsync_argv,
    rsync_argv_for as _rsync_argv_for, rsync_command_binary as _rsync_command_binary,
    rsync_path_arg as _rsync_path_arg, rsync_supports as _rsync_supports, run_rsync as _run_rsync,
    under_valgrind as _under_valgrind,
)

def rsync_command_binary(cmd: str = None) -> str:
    return _rsync_command_binary(environment.RSYNC if cmd is None else cmd)

def under_valgrind():
    return _under_valgrind(environment.RSYNC, environment.RSYNC_PEER)

def rsh_cmd(cmd: str = None, *opts: str) -> str:
    command = str(environment.SRCDIR / 'support' / 'lsh.sh') if cmd is None else cmd
    return _rsh_cmd(command, *opts)

def rsync_path_arg(cmd: str = None) -> str:
    return _rsync_path_arg(environment.RSYNC_PEER if cmd is None else cmd)

def rsync_argv(*args: str) -> list:
    return _rsync_argv(environment.RSYNC, *args)

def rsync_argv_for(binary, *args: str, command: str = None) -> list:
    return _rsync_argv_for(environment.RSYNC if command is None else command, binary, *args)

def rsync_supports(flag: str) -> bool:
    return _rsync_supports(environment.RSYNC, flag)

def forced_protocol():
    return _forced_protocol(environment.RSYNC)

def run_rsync(*args: str, check: bool = True,
              capture_output: bool = False) -> subprocess.CompletedProcess:
    return _run_rsync(environment.RSYNC, *args, check=check, capture_output=capture_output)

def rsync_getgroups() -> list:
    out = subprocess.check_output([str(environment.TOOLDIR / 'getgroups')], text=True)
    return out.split()

def runtest(label: str, fn, *args, **kwargs):
    print(f"Test {label}: ", end="", flush=True)
    fn(*args, **kwargs)
    print("done.")
