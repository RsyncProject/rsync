#!/usr/bin/env python3
# A daemon with `numeric ids = no` and a `name converter` maps the user/group
# names a sender puts in the file list by piping each name to the converter
# helper as a whitespace-and-newline-delimited token. A malicious sender that
# sends a user name containing a literal newline could inject an extra token (or
# a forged converter response) into that pipe. namecvt_call() (clientserver.c)
# guards this with namecvt_safe_token(), which refuses any token containing a
# control char (< 0x20 or 0x7F) before writing it to the converter.
#
# This drives the attack with the pure-Python sender rather than a recompiled
# malicious binary: push one file-list entry whose XMIT_USER_NAME_FOLLOWS name
# is "bad\nname". recv_user_name() -> recv_add_id() -> user_to_uid() ->
# namecvt_call() runs during file-list receipt, so no transfer phase is needed.
#
# Oracle: the converter helper must NOT receive a split "name" token (the
# newline never reached the pipe) AND the daemon must report "invalid
# name-converter token" (it refused the control char). Needs a real TCP daemon.

import time

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, start_test_daemon,
    test_fail, write_daemon_conf, xattrs_supported,
)
import rsync_proto as rp

PORT = 12974
require_tcp("the pure-Python sender needs a real TCP daemon; run with --use-tcp")
claim_ports(PORT)

base = SCRATCHDIR / 'daemon-namecvt-newline'
rmtree(base)
dest = base / 'module'
makepath(dest)

cvt_log = base / 'namecvt.log'
converter = base / 'nameconvert'
converter.write_text(
    "#!/usr/bin/env python3\n"
    "import sys\n"
    f"log = open({str(cvt_log)!r}, 'a', buffering=1)\n"
    "for line in sys.stdin:\n"
    "    log.write(line)\n"
    "    print('123', flush=True)\n")
converter.chmod(0o755)

daemon_log = base / 'namecvt-daemon.log'
params = {
    'path': str(dest),
    'read only': 'no',
    'use chroot': 'no',
    'numeric ids': 'no',
    'name converter': str(converter),
    'log file': str(daemon_log),
}
if xattrs_supported():
    params['fake super'] = 'yes'
conf = write_daemon_conf([('recv', params)], name='namecvt-newline.conf')
url = start_test_daemon(conf, PORT)

s = rp.DaemonSender('127.0.0.1', PORT)
# -o => preserve_uid, so the receiver reads the uid + user name and maps it.
s.handshake('recv', ['--server', '-oe.LsfxCIu', '.', 'recv/'], greeting_version=30)
# One entry whose owner name carries a literal newline.
s.send_flat_flist([rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=0,
                                uid=1, user_name='bad\nname')])
back = s.drain(timeout=3.0)
s.close()

rejected = b'invalid name-converter token' in back
for _ in range(50):
    if daemon_log.exists() and 'invalid name-converter token' in daemon_log.read_text(errors='replace'):
        rejected = True
        break
    if rejected:
        break
    time.sleep(0.1)

cvt_lines = cvt_log.read_text().splitlines() if cvt_log.exists() else []
if any(line == 'name' for line in cvt_lines):
    test_fail("name converter received the newline-split tail of a malicious "
              f"sender user name. Logged requests: {cvt_lines!r}")
if not rejected:
    dlog = daemon_log.read_text(errors='replace') if daemon_log.exists() else ''
    test_fail("daemon accepted a sender-supplied user name containing a newline "
              "without reporting the name-converter token rejection.\n"
              f"client bytes:\n{back[:400]!r}\ndaemon log:\n{dlog}\n"
              f"converter requests: {cvt_lines!r}")

print("daemon-namecvt-newline-token: daemon rejects newline-bearing "
      "name-converter tokens")
