"""Regression test for daemon log file silent failures with process substitution and pipes."""

import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rsyncfns import makepath, rmtree, rsync_argv, test_fail, test_skipped

if not sys.platform.startswith('linux'):
    test_skipped('Namespace daemon testing is a Linux-specific feature')
    raise SystemExit(0)

bash = shutil.which('bash')
if not bash:
    test_skipped('bash is not installed')
    raise SystemExit(0)

probe_bash = subprocess.run([bash, '-c', 'echo "probe" > >(cat > /dev/null)'], capture_output=True)
if probe_bash.returncode != 0:
    test_skipped('bash process substitution is not supported on this system')
    raise SystemExit(0)

def kill_daemon(proc):
    """Safely terminate the daemon process group if it is still running."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

ws_base = Path(tempfile.mkdtemp(prefix='rsync-pseudo-paths-daemon-'))
ws_base.chmod(0o777)

try:
    ws_src = ws_base / 'src'
    makepath(ws_src)
    ws_src.chmod(0o777)

    tf = ws_src / 'file.txt'
    tf.write_text('data\n')
    tf.chmod(0o777)

    rsync_bin = rsync_argv()[0]
    if not Path(rsync_bin).exists():
        test_fail(f"rsync binary not found at {rsync_bin}")

    base_port = 20000 + (os.getpid() % 10000)

    # -------------------------------------------------------------------------
    # TEST 1: Host Daemon (Normal Root) with > >(...) process substitution
    # -------------------------------------------------------------------------
    port_host = base_port
    print(f"Running Test 1: Host Daemon (Normal Root) with > >(cat > out) (Port {port_host})...", flush=True)

    dest_host = ws_base / 'dest_host'
    makepath(dest_host)
    dest_host.chmod(0o777)

    out_host = ws_base / 'out_host'
    err_host = ws_base / 'err_host'
    conf_host = ws_base / 'host.conf'

    conf_host.write_text(f"""pid file = {ws_base}/host.pid
log file = /dev/stdout
[test-from]
path = {dest_host}
read only = no
use chroot = no
""")

    cmd_host = f"{rsync_bin} --daemon --no-detach --config={conf_host} --port={port_host} --address=127.0.0.1 > >(cat > {out_host}) 2> {err_host} < /dev/null"
    
    # Executes directly as the host user
    daemon_host = subprocess.Popen([bash, '-c', cmd_host], start_new_session=True)
    
    try:
        # Bounded readiness polling (Wait for daemon to bind to the port)
        for _ in range(50):
            if daemon_host.poll() is not None:
                test_fail("Host Daemon crashed immediately upon startup.")
            try:
                with socket.create_connection(('127.0.0.1', port_host), timeout=0.1):
                    break # Port is open, daemon is ready
            except OSError:
                time.sleep(0.1)
        else:
            test_fail("Host Daemon failed to bind to port within the timeout period.")

        client_cmd_host = [rsync_bin, '-a', str(ws_src) + '/', f'rsync://127.0.0.1:{port_host}/test-from/']
        client_proc = subprocess.run(client_cmd_host, capture_output=True, text=True)
        
        if client_proc.returncode != 0:
            test_fail(f"Client transfer failed against Host Daemon. Stderr: {client_proc.stderr.strip()}")

        # Poll up to a second for logs to flush through the pipe
        for _ in range(10):
            out_host_data = out_host.read_text() if out_host.exists() else ""
            if "rsyncd version" in out_host_data and "test-from" in out_host_data:
                break
            time.sleep(0.1)

    finally:
        kill_daemon(daemon_host)

    out_host_data = out_host.read_text() if out_host.exists() else ""
    err_host_data = err_host.read_text() if err_host.exists() else ""

    if "rsyncd version" not in out_host_data or "test-from" not in out_host_data:
        test_fail(f"Bug reproduced: Host Daemon silently dropped logs.\n'out' file: {out_host_data}\n'err' file: {err_host_data}")

    print("Test 1 Passed: Daemon successfully logged to out file on host.", flush=True)

    # -------------------------------------------------------------------------
    # TEST 2 SETUP: Namespace capabilities and configuration
    # -------------------------------------------------------------------------
    unshare = shutil.which('unshare')
    if not unshare:
        print("Test 2 Skipped: unshare is not installed", flush=True)
        raise SystemExit(0)

    launcher = []
    if os.geteuid() == 0:
        setpriv = shutil.which('setpriv')
        if setpriv is None:
            print("Test 2 Skipped: setpriv is unavailable for the root-run testsuite", flush=True)
            raise SystemExit(0)
        launcher = [setpriv, '--reuid=65534', '--regid=65534', '--clear-groups']

    unshare_argv = [unshare, '--user', '--map-root-user', '--mount', '--pid', '--fork', '--mount-proc']

    probe_unshare = subprocess.run(launcher + unshare_argv + ['true'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe_unshare.returncode != 0:
        print("Test 2 Skipped: user namespace is unavailable with the required unprivileged launcher", flush=True)
        raise SystemExit(0)

    check_ns = (
        "import os, sys\n"
        "proc_uid = os.lstat('/proc/self').st_uid\n"
        "if proc_uid in (0, os.geteuid()):\n"
        "    sys.exit(22)\n"
    )
    probe_uid = subprocess.run(launcher + unshare_argv + [sys.executable, '-c', check_ns])
    if probe_uid.returncode == 22:
        print("Test 2 Skipped: /proc/self does not expose an overflow uid in this namespace", flush=True)
        raise SystemExit(0)
    elif probe_uid.returncode != 0:
        test_fail(f'Namespace uid check failed (rc={probe_uid.returncode})')

    ns_rsync_bin = ws_base / 'rsync-bin'
    shutil.copy2(rsync_bin, ns_rsync_bin)
    ns_rsync_bin.chmod(0o777)

    # -------------------------------------------------------------------------
    # TEST 2: Namespace Daemon with > >(...) process substitution
    # -------------------------------------------------------------------------
    port_ns = base_port + 1
    print(f"Running Test 2: Namespace Daemon inside unshare --user (Port {port_ns})...", flush=True)

    dest_ns = ws_base / 'dest_ns'
    makepath(dest_ns)
    dest_ns.chmod(0o777)

    out_ns = ws_base / 'out_ns'
    err_ns = ws_base / 'err_ns'
    conf_ns = ws_base / 'ns.conf'

    conf_ns.write_text(f"""pid file = {ws_base}/ns.pid
log file = /dev/stdout
[test-from]
path = {dest_ns}
read only = no
use chroot = no
""")

    cmd_ns = f"{ns_rsync_bin} --daemon --no-detach --config={conf_ns} --port={port_ns} --address=127.0.0.1 > >(cat > {out_ns}) 2> {err_ns} < /dev/null"
    
    namespace_cmd = launcher + unshare_argv + [bash, '-c', cmd_ns]
    daemon_ns = subprocess.Popen(namespace_cmd, stdin=subprocess.DEVNULL, start_new_session=True)
    
    try:
        for _ in range(50):
            if daemon_ns.poll() is not None:
                test_fail("Namespace Daemon crashed immediately upon startup.")
            try:
                with socket.create_connection(('127.0.0.1', port_ns), timeout=0.1):
                    break # Port is open, daemon is ready
            except OSError:
                time.sleep(0.1)
        else:
             test_fail("Namespace Daemon failed to bind to port within the timeout.")

        time.sleep(0.2)

    finally:
        kill_daemon(daemon_ns)

    out_ns_data = out_ns.read_text() if out_ns.exists() else ""
    err_ns_data = err_ns.read_text() if err_ns.exists() else ""

    if "rsyncd version" not in out_ns_data:
        test_fail(f"Bug reproduced: Namespace Daemon silently dropped logs.\n'out' file: {out_ns_data}\n'err' file: {err_ns_data}")

    print("Test 2 Passed: Daemon successfully logged inside unprivileged namespace.", flush=True)

finally:
    rmtree(ws_base)

raise SystemExit(0)
