"""Launch one rsync daemon session over a local socketpair."""

import socket
import subprocess

import rsync_proto as rp

from rsyncfns import rsync_argv


def _client_for_socket(sock, timeout=10):
    client = object.__new__(rp.DaemonClient)
    client.sock = sock
    client.sock.settimeout(timeout)
    client.protocol = rp.DEFAULT_PROTOCOL
    client.compat_flags = None
    client.seed = None
    client.xfer_sum_len = 16
    client._rbuf = b''
    client._ndx_prev_positive = -1
    client._ndx_prev_negative = 1
    client._mux_in = b''
    client._r_ndx_prev_positive = -1
    client._r_ndx_prev_negative = 1
    client.messages = []
    return client


def start_stdio_daemon(conf, timeout=10, env=None):
    """Return ``(DaemonClient, Popen)`` for one daemon connection."""
    parent, child = socket.socketpair()
    try:
        proc = subprocess.Popen(
            rsync_argv('--daemon', '--no-detach', f'--config={conf}'),
            stdin=child.fileno(), stdout=child.fileno(),
            stderr=subprocess.PIPE, close_fds=True, env=env,
        )
    except Exception:
        parent.close()
        child.close()
        raise
    child.close()
    return _client_for_socket(parent, timeout), proc


def finish_stdio_daemon(client, proc, timeout=5):
    """Close a test session and return the daemon's stderr text."""
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

