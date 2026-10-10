import functools
import os
import re
import shlex
import subprocess
import sys

from .results import test_fail

def split_rsync_cmd(command: str) -> list:
    if os.path.isfile(command):
        return [command]
    for match in reversed(list(re.finditer(r'\s+', command))):
        executable = command[:match.start()]
        if os.path.isfile(executable):
            return [executable] + shlex.split(command[match.start():])
    return shlex.split(command)

def rsync_command_binary(command: str) -> str:
    for argument in reversed(split_rsync_cmd(command)):
        if os.path.isfile(argument):
            return argument
    raise ValueError('rsync command contains no executable file')

def under_valgrind(*commands: str) -> bool:
    return any(os.path.basename(split_rsync_cmd(command)[0]) == 'valgrind' for command in commands)

def rsh_cmd(command: str, *options: str) -> str:
    return shlex.join([command, *options])

def rsync_path_arg(command: str) -> str:
    return shlex.join(split_rsync_cmd(command))

def rsync_argv(command: str, *args: str) -> list:
    return split_rsync_cmd(command) + list(args)

def rsync_argv_for(command: str, binary, *args: str) -> list:
    argv = split_rsync_cmd(command)
    argv[argv.index(rsync_command_binary(command))] = os.fspath(binary)
    return argv + list(args)

def run_rsync(command: str, *args: str, check: bool = True,
              capture_output: bool = False) -> subprocess.CompletedProcess:
    argv = rsync_argv(command, *args)
    result = subprocess.run(argv, capture_output=capture_output, text=capture_output)
    if check and result.returncode != 0:
        test_fail(f"rsync exited {result.returncode}: {' '.join(argv)}")
    return result

@functools.lru_cache(maxsize=64)
def rsync_supports(command: str, flag: str) -> bool:
    try:
        result = subprocess.run(rsync_argv(command, flag, '--version'),
                                capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return True
    if result.returncode == 0:
        return True
    stderr = (result.stderr or '').lower()
    return not any(marker in stderr for marker in (
        'unknown option', 'unrecognized option', 'no such option',
    ))

def forced_protocol(command: str):
    argv = split_rsync_cmd(command)
    for index, argument in enumerate(argv):
        if argument.startswith('--protocol='):
            value = argument.partition('=')[2]
        elif argument == '--protocol' and index + 1 < len(argv):
            value = argv[index + 1]
        else:
            continue
        return int(value) if value.isdigit() else None
    return None

def process_thread_count(pid: int) -> int:
    if sys.platform == 'darwin':
        result = subprocess.run(['ps', '-M', str(pid)], capture_output=True, text=True)
        return max(0, len(result.stdout.splitlines()) - 1)
    if sys.platform.startswith('linux'):
        try:
            with open(f'/proc/{pid}/status') as stream:
                status = stream.read()
        except OSError:
            return 0
        match = re.search(r'^Threads:\s+(\d+)', status, re.M)
        return int(match.group(1)) if match else 0
    return -1
