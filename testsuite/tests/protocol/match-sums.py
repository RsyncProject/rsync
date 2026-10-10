#!/usr/bin/env python3

import glob
import os

from harness import protocol as rp
from harness.rsync import SCRATCHDIR, claim_ports, require_asan, require_tcp, start_rsyncd, test_fail

PORT = 12951
require_tcp('the protocol generator requires TCP')
require_asan('malformed sum handling is detected through AddressSanitizer')
claim_ports(PORT)

module = SCRATCHDIR / 'match-sums-module'
module.mkdir(parents=True)
(module / 'empty').write_bytes(b'')
(module / 'byte').write_bytes(b'A')
config = SCRATCHDIR / 'match-sums.conf'
config.write_text(f'''pid file = {SCRATCHDIR}/match-sums.pid
use chroot = no

[module]
    path = {module}
    read only = no
''')
log = SCRATCHDIR / 'match-sums-asan'
for stale in glob.glob(f'{log}.*'):
    os.unlink(stale)
options = os.environ.get('ASAN_OPTIONS', '')
os.environ['ASAN_OPTIONS'] = ((options + ':') if options else '') + (
    f'detect_leaks=0:abort_on_error=1:log_path={log}')
start_rsyncd(config, PORT)

def send(name, options, request):
    client = rp.DaemonClient('127.0.0.1', PORT)
    client.handshake('module', ['--server', '--sender', '-e.LsfxCIu', *options,
                                '.', f'module/{name}'], greeting_version=30)
    entries = rp.sort_entries(client.recv_flist(preserve_links=False))
    index = next(i for i, entry in enumerate(entries) if entry.name == name.encode())
    client.send_data(request(client, index))
    client.send_data(client.w_ndx(rp.NDX_DONE))
    client.drain(timeout=3)
    client.close()

def append_request(client, index):
    return (client.w_ndx(index) + rp.w_shortint(rp.ITEM_TRANSFER)
            + rp.w_sum_head(1, 1, 0, 0))

def adjacent_request(client, index):
    checksum = rp.get_checksum1(b'A')
    return (client.w_ndx(index) + rp.w_shortint(rp.ITEM_TRANSFER)
            + rp.w_sum_head(2, 2, 0, 1) + rp.w_int(checksum) * 2)

send('empty', ['--append-verify'], append_request)
send('byte', [], adjacent_request)
reports = ''.join(open(path, errors='replace').read() for path in glob.glob(f'{log}.*'))
if 'AddressSanitizer' in reports:
    test_fail(f'malformed sum headers caused a memory error:\n{reports[:1500]}')

print('malformed sum headers do not dereference empty mappings')
