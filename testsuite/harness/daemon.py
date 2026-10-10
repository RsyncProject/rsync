import atexit
import fcntl
import os
import shlex
import signal
import socket
import stat
import struct
import subprocess
import sys
import time

from . import protocol
from .process import rsync_argv, rsync_command_binary, rsync_path_arg, split_rsync_cmd
from .results import test_fail, test_skipped

RSYNC_PEER = os.environ.get('RSYNC_PEER', os.environ.get('RSYNC', 'rsync'))
USE_TCP = os.environ.get('RSYNC_TEST_USE_TCP') == '1'

_RSYNC_NAMES = {'rsync', 'rsync.exe'}
for _command in (os.environ.get('RSYNC'), RSYNC_PEER):
    if not _command:
        continue
    try:
        _RSYNC_NAMES.add(os.path.basename(rsync_command_binary(_command)))
    except ValueError:
        pass

_LOCK_PATH = '/tmp/rsync_test.lck'
_LOCK_MAGIC = 0x9D4F2B8A
_PID_BASE = 1 << 16
_PID_SIZE = 8
_lock_fd = None
_reaped_stale = False

def _check_magic(fd):
    fcntl.lockf(fd, fcntl.LOCK_EX, 4, 0)
    try:
        record = os.pread(fd, 4, 0)
        current = struct.unpack('=I', record)[0] if len(record) == 4 else 0
        if current == 0:
            os.pwrite(fd, struct.pack('=I', _LOCK_MAGIC), 0)
        elif current != _LOCK_MAGIC:
            os.close(fd)
            test_fail(f'lock file {_LOCK_PATH} has layout magic {current:#010x}, '
                      f'expected {_LOCK_MAGIC:#010x}; remove the stale file')
    except (OSError, struct.error) as error:
        test_fail(f'cannot verify lock file {_LOCK_PATH}: {error}')
    finally:
        try:
            fcntl.lockf(fd, fcntl.LOCK_UN, 4, 0)
        except OSError:
            pass

def _open_lock():
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR | nofollow, 0o666)
    except FileExistsError:
        fd = None
    if fd is not None:
        try:
            os.fchmod(fd, 0o666)
        except OSError:
            pass
        _check_magic(fd)
        return fd
    try:
        fd = os.open(_LOCK_PATH, os.O_RDWR | nofollow)
    except OSError as error:
        test_fail(f'cannot open lock file {_LOCK_PATH}: {error}')
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        test_fail(f'lock file {_LOCK_PATH} is not a regular file with one link')
    _check_magic(fd)
    return fd

def _record_process(port, process_group, pid):
    if _lock_fd is None:
        return
    try:
        os.pwrite(_lock_fd, struct.pack('=ii', process_group, pid),
                  _PID_BASE + port * _PID_SIZE)
    except (OSError, struct.error):
        pass

def _read_process(port):
    if _lock_fd is None:
        return 0, 0
    try:
        record = os.pread(_lock_fd, _PID_SIZE, _PID_BASE + port * _PID_SIZE)
        if len(record) != _PID_SIZE:
            return 0, 0
        process_group, pid = struct.unpack('=ii', record)
    except (OSError, struct.error):
        return 0, 0
    return (process_group, pid) if pid > 1 else (0, 0)

def _is_rsync(pid):
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    for argv in (['ps', '-p', str(pid), '-o', 'comm='], ['ps', '-p', str(pid)]):
        try:
            result = subprocess.run(argv, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode == 0:
            lines = [line.split() for line in result.stdout.splitlines() if line.split()]
            return bool(lines and os.path.basename(lines[-1][-1]) in _RSYNC_NAMES)
    return False

def _wait_gone(pid, timeout):
    deadline = time.monotonic() + timeout
    while _is_rsync(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True

def _kill_windows_process(pid):
    if not sys.platform.startswith('cygwin'):
        return
    try:
        result = subprocess.run(['ps', '-W'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0] == str(pid):
            try:
                subprocess.run(['taskkill', '/F', '/PID', fields[3]],
                               capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
            return

def _reap_group(process_group, pid):
    if not _is_rsync(pid):
        return False
    try:
        if process_group > 1 and process_group != os.getpgrp():
            os.killpg(process_group, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if _wait_gone(pid, 2):
        return True
    _kill_windows_process(pid)
    return _wait_gone(pid, 2)

def _reap_port(port):
    process_group, pid = _read_process(port)
    if not _reap_group(process_group, pid):
        return False
    _record_process(port, 0, 0)
    time.sleep(0.2)
    return True

def _reap_stale():
    if _lock_fd is None:
        return
    try:
        size = os.fstat(_lock_fd).st_size
        region = os.pread(_lock_fd, max(0, size - _PID_BASE), _PID_BASE)
    except OSError:
        return
    for port in range(min(len(region) // _PID_SIZE, 65536)):
        start = port * _PID_SIZE
        process_group, pid = struct.unpack('=ii', region[start:start + _PID_SIZE])
        if pid <= 1:
            continue
        try:
            fcntl.lockf(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, port)
        except OSError:
            continue
        try:
            if _reap_group(process_group, pid):
                _record_process(port, 0, 0)
        finally:
            try:
                fcntl.lockf(_lock_fd, fcntl.LOCK_UN, 1, port)
            except OSError:
                pass

def _claim_setup():
    global _lock_fd, _reaped_stale
    if _lock_fd is None:
        _lock_fd = _open_lock()
    if not _reaped_stale:
        _reaped_stale = True
        _reap_stale()

def _bindable(port, reaped=False, fatal=True):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(('127.0.0.1', port))
        return True
    except OSError as error:
        failure = error
    finally:
        listener.close()
    if not reaped and _reap_port(port):
        return _bindable(port, reaped=True, fatal=fatal)
    if not fatal:
        return False
    test_fail(f'port {port} is still bound to 127.0.0.1: {failure.strerror}')
    return False

def claim_ports(*ports):
    _claim_setup()
    for port in sorted(ports):
        fcntl.lockf(_lock_fd, fcntl.LOCK_EX, 1, port)
        _bindable(port)

def claim_free_port(preferred):
    _claim_setup()
    candidates = [preferred + offset for offset in (0, 1000, 2000, 3000, 4000)]
    for port in candidates:
        if 1024 < port < 65536:
            fcntl.lockf(_lock_fd, fcntl.LOCK_EX, 1, port)
            if _bindable(port, fatal=False):
                return port
    test_fail(f'no usable TCP port near {preferred}: {", ".join(map(str, candidates))}')
    return preferred

def _set_parent_death_signal():
    if not sys.platform.startswith('linux'):
        return
    try:
        import ctypes
        ctypes.CDLL('libc.so.6', use_errno=True).prctl(1, 15, 0, 0, 0)
    except OSError:
        pass

def _children(pid):
    argv = (['ps', '-W'] if sys.platform.startswith('cygwin')
            else ['ps', '-A', '-o', 'pid=,ppid='])
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    children = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == str(pid):
            try:
                children.append(int(fields[0]))
            except ValueError:
                pass
    return children

def _kill(pid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _is_rsync(pid):
            return
        try:
            os.kill(pid, sig)
        except OSError:
            return
        time.sleep(0.3)
    if _is_rsync(pid):
        _kill_windows_process(pid)

def _stop(proc):
    if proc.poll() is not None:
        return
    children = _children(proc.pid)
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
        _kill_windows_process(proc.pid)
    except OSError:
        pass
    for pid in children:
        _kill(pid)

def _cleanup(proc, port):
    _stop(proc)
    if not _is_rsync(proc.pid):
        _record_process(port, 0, 0)

def start_rsyncd(config, port, rsync_cmd=None, stdin=subprocess.DEVNULL,
                 address='127.0.0.1'):
    if address not in ('127.0.0.1', '::1'):
        test_fail(f'refusing non-loopback test daemon address {address!r}')
    argv = split_rsync_cmd(rsync_cmd or RSYNC_PEER) + [
        '--daemon', '--no-detach', f'--address={address}', f'--port={port}',
        f'--config={config}',
    ]
    proc = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            preexec_fn=_set_parent_death_signal)
    _record_process(port, os.getpgrp(), proc.pid)
    atexit.register(_cleanup, proc, port)
    deadline = time.monotonic() + 10
    failure = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            test_fail(f'rsyncd exited before listening on port {port}: {proc.returncode}')
        try:
            with socket.create_connection((address, port), timeout=0.5):
                return proc
        except OSError as error:
            failure = error
            time.sleep(0.05)
    _stop(proc)
    test_fail(f'rsyncd never listened on {address}:{port}: {failure}')

def start_test_daemon(config, port, rsync_cmd=None):
    command = rsync_cmd or RSYNC_PEER
    if USE_TCP:
        port = claim_free_port(port)
        start_rsyncd(config, port, command)
        return f'rsync://localhost:{port}/'
    os.environ['RSYNC_CONNECT_PROG'] = (
        f'{rsync_path_arg(command)} --config={shlex.quote(str(config))} --daemon')
    return 'rsync://localhost/'

def probe_module(url, module):
    return subprocess.run(
        rsync_argv(os.environ['RSYNC'], '-r', f'{url}{module}/'),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    ).returncode

def require_tcp(reason):
    if not USE_TCP:
        test_skipped(reason, capability='tcp')

def require_asan(reason, command=None):
    argv = split_rsync_cmd(command or RSYNC_PEER)
    try:
        result = subprocess.run(argv + ['--version'],
                                env={**os.environ, 'ASAN_OPTIONS': 'help=1'},
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=15)
    except (OSError, subprocess.SubprocessError):
        test_skipped(reason, capability='asan')
        return
    if b'AddressSanitizer' not in result.stderr:
        test_skipped(reason, capability='asan')

def start_stdio_daemon(config, timeout=10, env=None):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent = None
    try:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        parent = socket.create_connection(listener.getsockname(), timeout)
        child, _ = listener.accept()
    except OSError:
        if parent is not None:
            parent.close()
        raise
    finally:
        listener.close()
    try:
        proc = subprocess.Popen(
            rsync_argv(RSYNC_PEER, '--daemon', '--no-detach', f'--config={config}'),
            stdin=child.fileno(), stdout=child.fileno(), stderr=subprocess.PIPE,
            close_fds=True, env=env,
        )
    except (OSError, ValueError):
        parent.close()
        child.close()
        raise
    child.close()
    return protocol.DaemonClient.from_socket(parent, timeout), proc

def finish_stdio_daemon(client, proc, timeout=5):
    try:
        client.drain(timeout=0.25)
    except OSError:
        pass
    client.close()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    return proc.stderr.read().decode('utf-8', 'replace') if proc.stderr else ''
