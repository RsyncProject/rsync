#!/usr/bin/env python3

import time

from harness import protocol as rp
from harness.rsync import SCRATCHDIR, claim_ports, require_tcp, start_rsyncd, test_fail

PORTS = 12957, 12958, 12959, 12960, 12967
require_tcp('the protocol sender requires TCP')
claim_ports(*PORTS)

def check(name, port, arguments, attack, refusal, reached=None):
    module = SCRATCHDIR / f'{name}-module'
    module.mkdir(parents=True)
    log = SCRATCHDIR / f'{name}.log'
    config = SCRATCHDIR / f'{name}.conf'
    config.write_text(f'''pid file = {SCRATCHDIR}/{name}.pid
use chroot = no
log file = {log}

[module]
    path = {module}
    read only = no
''')
    start_rsyncd(config, port)
    before = log.read_text(errors='replace') if log.exists() else ''
    sender = rp.DaemonSender('127.0.0.1', port)
    sender.handshake('module', arguments, greeting_version=30)
    try:
        attack(sender)
        sender.drain(timeout=3)
    except (OSError, rp.ProtocolError):
        pass
    sender.close()

    output = ''
    for _ in range(50):
        output = log.read_text(errors='replace')[len(before):]
        if refusal in output and (reached is None or reached in output):
            return
        time.sleep(0.1)
    test_fail(f'{name}: expected {refusal!r} in daemon log:\n{output}')

def cleared_index(sender):
    duplicate = rp.FileEntry('dup', mode=rp.S_IFREG | 0o644, length=0)
    sender.send_flat_flist([duplicate, duplicate])
    sender.send_transfer_ndx(1, iflags=0)

def freed_sublist(sender):
    directory = rp.FileEntry('d', mode=rp.S_IFDIR | 0o755, extra_flags=rp.XMIT_TOP_DIR)
    sender.send_data(directory.encode() * 2 + rp.end_of_flist(0, sender.protocol))
    sender.send_ndx_done()
    sender.send_subflist_marker(0)

def cleared_directory(sender):
    directory = rp.FileEntry('d', mode=rp.S_IFDIR | 0o755, extra_flags=rp.XMIT_TOP_DIR)
    unfinished = rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=100)
    child = rp.FileEntry('x', mode=rp.S_IFREG | 0o644)
    sender.send_data(directory.encode() * 2 + unfinished.encode()
                     + rp.end_of_flist(0, sender.protocol))
    sender.send_data(sender.w_ndx(rp.NDX_FLIST_OFFSET - 1) + child.encode()
                     + rp.end_of_flist(0, sender.protocol))

def undeclared_hardlink(sender):
    directory = rp.FileEntry('d', mode=rp.S_IFDIR | 0o755, extra_flags=rp.XMIT_TOP_DIR)
    unfinished = rp.FileEntry('f', mode=rp.S_IFREG | 0o644, length=100)
    hardlink = rp.FileEntry('d/h', mode=rp.S_IFREG | 0o644, hlink_ndx=1)
    sender.send_data(directory.encode() + unfinished.encode()
                     + rp.end_of_flist(0, sender.protocol))
    sender.send_data(sender.w_ndx(rp.NDX_FLIST_OFFSET) + hardlink.encode()
                     + rp.end_of_flist(0, sender.protocol))

def invalid_root(sender):
    dot = rp.FileEntry('.', mode=rp.S_IFREG | 0o644, length=100)
    other = rp.FileEntry('a', mode=rp.S_IFREG | 0o644, length=100)
    sender.send_data(dot.encode() + other.encode() + rp.end_of_flist(0, sender.protocol))
    time.sleep(0.5)
    try:
        sender.send_transfer_ndx(0, rp.ITEM_TRANSFER)
    except OSError:
        pass

check('cleared-index', PORTS[0], ['--server', '-e.LsfxCIu', '.', 'module/'],
      cleared_index, 'refusing transfer of cleared file index')
check('freed-sublist', PORTS[1], ['--server', '-re.iLsfxCIu', '.', 'module/'],
      freed_sublist, 'refusing sub-flist after final flist was freed')
check('cleared-directory', PORTS[2], ['--server', '-re.iLsfxCIu', '.', 'module/'],
      cleared_directory, 'refusing flist for cleared dir_ndx')
check('hardlink-gnum', PORTS[3], ['--server', '-rHe.iLsfxCIu', '.', 'module/'],
      undeclared_hardlink, 'hard-link gnum 1 precedes flist start')
check('invalid-root', PORTS[4], ['--server', '-re.iLsfxCIu', '.', 'module/'],
      invalid_root, 'rejecting non-directory transfer-root entry', 'receiving file list')
print('invalid file-list states are refused without crashing')
