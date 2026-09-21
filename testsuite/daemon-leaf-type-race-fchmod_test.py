#!/usr/bin/env python3
"""A leaf type race must not make the receiver chmod a directory.

The secure resolver probes a non-directory leaf with ``O_DIRECTORY`` and then
opens it again with the caller's flags.  A Darwin interposer pauses after the
second failed probe and asks a separately sandboxed path controller to replace
the regular file with a directory.  The sandbox proves that the controller can
rename the entries but cannot chmod the directory itself.  A vulnerable
receiver accepts the directory fd from the second open, changes it from 0755 to
0600, then leaves the change behind when its write-open fails with EISDIR.

Run via ``runtests.py --use-tcp`` outside an enclosing application sandbox.
"""

from __future__ import annotations

import errno
import os
import platform
import select
import shlex
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path


def racer_main(argv: list[str]) -> int:
    if len(argv) != 5:
        raise SystemExit("racer requires TARGET VICTIM OLD READY_FIFO DONE_FIFO")
    target, victim, old, ready_fifo, done_fifo = map(Path, argv)

    before = stat.S_IMODE(victim.stat().st_mode)
    denied_errno = 0
    try:
        victim.chmod(0o600)
    except OSError as exc:
        denied_errno = exc.errno or 0
    after = stat.S_IMODE(victim.stat().st_mode)
    denied = denied_errno in (errno.EPERM, errno.EACCES) and after == before
    print(
        "RACER_DIRECT_CHMOD"
        f" denied={denied} errno={denied_errno}"
        f" before={before:04o} after={after:04o}",
        flush=True,
    )
    if not denied:
        return 2

    with ready_fifo.open("rb", buffering=0) as ready:
        if ready.read(1) != b"R":
            return 3

    target.rename(old)
    victim.rename(target)
    mode_at_swap = stat.S_IMODE(target.stat().st_mode)
    print(
        "RACER_SWAP target_to_old=True victim_to_target=True"
        f" mode_before_ack={mode_at_swap:04o}",
        flush=True,
    )
    if mode_at_swap != before:
        return 4

    with done_fifo.open("wb", buffering=0) as done:
        done.write(b"D")
    return 0


# Enter helper mode before importing the test harness, which requires runner
# environment variables and owns the daemon-port bookkeeping.
if len(sys.argv) > 1 and sys.argv[1] == "--racer":
    sys.exit(racer_main(sys.argv[2:]))


from rsyncfns import (  # noqa: E402
    RSYNC,
    RSYNC_PEER,
    SCRATCHDIR,
    claim_ports,
    require_tcp,
    rmtree,
    test_fail,
    test_skipped, split_rsync_cmd,
)


PORT = 13213
SANDBOX_PROFILE = "(version 1)(allow default)(deny file-write-mode)"

INTERPOSER_SOURCE = r"""
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

static int probes;
static int synchronizing;

__attribute__((constructor))
static void race_interposer_loaded(void)
{
	static const char marker[] = "LEAF_TYPE_RACE interposer_loaded\n";
	(void)write(STDERR_FILENO, marker, sizeof marker - 1);
}

static int real_openat(int dfd, const char *path, int flags, mode_t mode)
{
	return (int)syscall(SYS_openat, dfd, path, flags, mode);
}

static int send_ready(const char *path)
{
	char byte = 'R';
	int fd = open(path, O_WRONLY);
	int ok = fd >= 0 && write(fd, &byte, 1) == 1;
	int saved = errno;
	if (fd >= 0)
		close(fd);
	errno = saved;
	return ok ? 0 : -1;
}

static int await_done(const char *path)
{
	char byte;
	int fd = open(path, O_RDONLY);
	int ok = fd >= 0 && read(fd, &byte, 1) == 1 && byte == 'D';
	int saved = errno;
	if (fd >= 0)
		close(fd);
	errno = saved;
	return ok ? 0 : -1;
}

static void synchronize_with_racer(void)
{
	const char *ready = getenv("LEAF_TYPE_RACE_READY_FIFO");
	const char *done = getenv("LEAF_TYPE_RACE_DONE_FIFO");
	static const char probe[] = "LEAF_TYPE_RACE probe_returned\n";
	static const char ack[] = "LEAF_TYPE_RACE racer_acknowledged\n";
	static const char failed[] = "LEAF_TYPE_RACE synchronization_failed\n";

	(void)write(STDERR_FILENO, probe, sizeof probe - 1);
	if (!ready || !done || send_ready(ready) < 0 || await_done(done) < 0) {
		(void)write(STDERR_FILENO, failed, sizeof failed - 1);
		return;
	}
	(void)write(STDERR_FILENO, ack, sizeof ack - 1);
}

static int race_openat(int dfd, const char *path, int flags, ...)
{
	const char *leaf = getenv("LEAF_TYPE_RACE_LEAF");
	const char *trigger_text = getenv("LEAF_TYPE_RACE_TRIGGER");
	mode_t mode = 0;
	int rc, saved, trigger = trigger_text ? atoi(trigger_text) : 2;

	if (flags & O_CREAT) {
		va_list ap;
		va_start(ap, flags);
		mode = (mode_t)va_arg(ap, int);
		va_end(ap);
	}
	rc = real_openat(dfd, path, flags, mode);
	saved = errno;
	if (!synchronizing && rc < 0 && (flags & O_DIRECTORY) && leaf
	 && strcmp(path, leaf) == 0 && ++probes == trigger) {
		synchronizing = 1;
		synchronize_with_racer();
		synchronizing = 0;
	}
	errno = saved;
	return rc;
}

__attribute__((used))
static const struct {
	const void *replacement;
	const void *replacee;
} race_openat_interpose __attribute__((section("__DATA,__interpose"))) = {
	(const void *)(uintptr_t)&race_openat,
	(const void *)(uintptr_t)&openat,
};
"""


def wait_for_port(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"daemon exited during startup: {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("timeout waiting for daemon listener")


def stop_process(process: subprocess.Popen, timeout: float = 3) -> tuple:
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=timeout)


if platform.system() != "Darwin":
    test_skipped("leaf-type authority proof currently requires Darwin")
if os.geteuid() == 0:
    test_skipped("leaf-type authority proof needs a non-root EACCES trigger")
sandbox_exec = Path("/usr/bin/sandbox-exec")
if not sandbox_exec.is_file():
    test_skipped("/usr/bin/sandbox-exec is unavailable")

require_tcp("leaf-type race needs an injected real TCP daemon")
claim_ports(PORT)

module = SCRATCHDIR / "module"
source = SCRATCHDIR / "source"
# A prior vulnerable run intentionally leaves module/target as a mode-0600
# directory.  Restore only that per-test object so a preserved scratch tree is
# removable on rerun.
if (module / "target").is_dir():
    (module / "target").chmod(0o700)
rmtree(module)
rmtree(source)
module.mkdir(parents=True)
source.mkdir()
(source / "target").write_bytes(b"replacement data\n")
target = module / "target"
target.write_bytes(b"read-only basis\n")
target.chmod(0o400)
victim = module / "victim"
victim.mkdir()
victim.chmod(0o755)
(victim / "marker").write_text("protected directory\n", encoding="utf-8")
victim_inode = victim.stat().st_ino
old = module / "old"
ready_fifo = SCRATCHDIR / "ready.fifo"
done_fifo = SCRATCHDIR / "done.fifo"
ready_fifo.unlink(missing_ok=True)
done_fifo.unlink(missing_ok=True)
os.mkfifo(ready_fifo)
os.mkfifo(done_fifo)

interposer_source = SCRATCHDIR / "leaf_type_race_interposer.c"
interposer = SCRATCHDIR / "leaf_type_race_interposer.dylib"
interposer_source.write_text(INTERPOSER_SOURCE, encoding="utf-8")
compile_result = subprocess.run(
    [
        os.environ.get("CC", "cc"),
        "-dynamiclib",
        "-O2",
        "-Wall",
        "-Wextra",
        "-o",
        os.fspath(interposer),
        os.fspath(interposer_source),
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
if compile_result.returncode != 0:
    test_fail(f"interposer compilation failed: {compile_result.stderr}")

racer_command = [
    os.fspath(sandbox_exec),
    "-p",
    SANDBOX_PROFILE,
    sys.executable,
    os.path.abspath(__file__),
    "--racer",
    os.fspath(target),
    os.fspath(victim),
    os.fspath(old),
    os.fspath(ready_fifo.resolve()),
    os.fspath(done_fifo.resolve()),
]
racer = subprocess.Popen(
    racer_command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
assert racer.stdout is not None
readable, _, _ = select.select([racer.stdout], [], [], 5)
if not readable:
    racer_stdout, racer_stderr = stop_process(racer)
    test_fail(
        "sandbox racer did not finish its authority preflight: "
        f"stdout={racer_stdout!r} stderr={racer_stderr!r}"
    )
preflight = racer.stdout.readline()
if "RACER_DIRECT_CHMOD denied=True" not in preflight:
    racer_stdout, racer_stderr = stop_process(racer)
    combined_stderr = racer_stderr or ""
    if "sandbox_apply: Operation not permitted" in combined_stderr:
        test_skipped("enclosing sandbox forbids nested sandbox-exec")
    test_fail(
        "sandbox racer retained chmod authority: "
        f"preflight={preflight!r} stdout={racer_stdout!r}"
        f" stderr={combined_stderr!r}"
    )

config = SCRATCHDIR / "leaf-type-race.conf"
config.write_text(
    f"pid file = {SCRATCHDIR / 'rsyncd.pid'}\n"
    f"lock file = {SCRATCHDIR / 'rsyncd.lock'}\n"
    f"log file = {SCRATCHDIR / 'rsyncd.log'}\n"
    "address = 127.0.0.1\n"
    f"port = {PORT}\n"
    "use chroot = no\n"
    "[mod]\n"
    f"    path = {module.resolve()}\n"
    "    read only = no\n"
    "    munge symlinks = no\n",
    encoding="utf-8",
)

daemon_env = os.environ.copy()
daemon_env.update(
    {
        "DYLD_INSERT_LIBRARIES": os.fspath(interposer),
        "DYLD_FORCE_FLAT_NAMESPACE": "1",
        "LEAF_TYPE_RACE_LEAF": "target",
        "LEAF_TYPE_RACE_TRIGGER": "2",
        "LEAF_TYPE_RACE_READY_FIFO": os.fspath(ready_fifo.resolve()),
        "LEAF_TYPE_RACE_DONE_FIFO": os.fspath(done_fifo.resolve()),
    }
)
daemon_command = split_rsync_cmd(RSYNC_PEER) + [
    "--daemon",
    "--no-detach",
    f"--config={config}",
]
daemon = subprocess.Popen(
    daemon_command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=daemon_env,
)

client = None
daemon_stdout = b""
daemon_stderr = b""
try:
    wait_for_port(daemon)
    client = subprocess.run(
        split_rsync_cmd(RSYNC)
        + [
            "-rt",
            "--inplace",
            "--ignore-times",
            "--contimeout=5",
            os.fspath(source / "target"),
            f"rsync://127.0.0.1:{PORT}/mod/target",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=20,
    )
finally:
    daemon_stdout, daemon_stderr = stop_process(daemon)

racer_tail, racer_stderr = stop_process(racer)
racer_stdout = preflight + (racer_tail or "")
daemon_text = (daemon_stderr or b"").decode("utf-8", "replace")
target_stat = target.stat() if target.exists() else None
target_mode = stat.S_IMODE(target_stat.st_mode) if target_stat else -1
result_line = (
    "RACE_RESULT"
    f" client_rc={client.returncode if client else 'missing'}"
    f" target_type={'directory' if target.is_dir() else 'other'}"
    f" target_mode={target_mode:04o}"
    f" old_is_file={old.is_file()} victim_exists={victim.exists()}"
)

confirmed = (
    client is not None
    and client.returncode != 0
    and "RACER_SWAP" in racer_stdout
    and "mode_before_ack=0755" in racer_stdout
    and "LEAF_TYPE_RACE probe_returned" in daemon_text
    and "LEAF_TYPE_RACE racer_acknowledged" in daemon_text
    and target.is_dir()
    and target_mode == 0o600
    and old.is_file()
    and not victim.exists()
    and target_stat is not None
    and target_stat.st_ino == victim_inode
)

if confirmed:
    test_fail(
        "H013 CONFIRMED: a mode-denied path controller replaced the leaf after "
        "the directory probe, and the receiver persistently chmodded the "
        "substituted directory from 0755 to 0600\n"
        f"{racer_stdout}{daemon_text}{client.stdout if client else ''}"
        f"{result_line}\n"
    )

if "LEAF_TYPE_RACE interposer_loaded" not in daemon_text:
    test_fail("invalid leaf-type race control: the daemon did not load the interposer")
if "LEAF_TYPE_RACE synchronization_failed" in daemon_text:
    test_fail(
        "invalid leaf-type race control: synchronization failed\n"
        f"{racer_stdout}{daemon_text}{result_line}\n"
    )
if "LEAF_TYPE_RACE probe_returned" in daemon_text and "RACER_SWAP" not in racer_stdout:
    test_fail(
        "invalid leaf-type race control: the probe fired without a completed swap\n"
        f"{racer_stdout}{daemon_text}{result_line}\n"
    )
if "LEAF_TYPE_RACE probe_returned" not in daemon_text and (
    not target.is_file()
    or stat.S_IMODE(victim.stat().st_mode) != 0o755
):
    test_fail(
        "invalid no-probe control: the old recovery path changed an entry\n"
        f"{racer_stdout}{daemon_text}{client.stdout if client else ''}"
        f"{result_line}\n"
    )

if "RACER_SWAP" in racer_stdout and target_mode != 0o755:
    test_fail(
        "the raced victim directory changed unexpectedly: "
        f"mode={target_mode:04o}\n{racer_stdout}{daemon_text}"
    )

print("leaf type validation prevented chmod of the substituted directory")
