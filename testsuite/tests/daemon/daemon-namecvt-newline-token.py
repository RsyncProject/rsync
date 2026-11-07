#!/usr/bin/env python3

import time

from harness.daemon_config import write_daemon_conf
from harness.rsync import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, start_test_daemon, test_fail,
    xattrs_supported,
)
from harness import protocol as rp

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
s.handshake('recv', ['--server', '-oe.LsfxCIu', '.', 'recv/'], greeting_version=30)
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
