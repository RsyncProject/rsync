#!/usr/bin/env python3
"""Protocol 29 must not let a sender enlarge scoped --delete.

The protocol-30 receiver downgrades an implied parent that a malicious sender
marks as a content directory.  The legacy branch has no XMIT_NO_CONTENT_DIR
bit and currently trusts XMIT_TOP_DIR, even though the receiver's implied
filter says that the directory is only a parent of the requested leaf.
"""

import socket
import subprocess
import threading

from rsyncfns import (
    SCRATCHDIR, claim_ports, makepath, require_tcp, rmtree, rsync_argv,
    test_fail,
)
import rsync_proto as rp


PORT = 13303

require_tcp('the malicious protocol-29 server needs TCP; run with --use-tcp')
claim_ports(PORT)

base = SCRATCHDIR / 'peer-legacy-implied-delete-scope'
rmtree(base)
makepath(base)


def pull(label, forged_top_dir):
    dest = base / label
    makepath(dest / 'dir')
    sentinel = dest / 'dir' / 'must-survive'
    sentinel.write_text('receiver-owned sibling outside requested leaf\n')

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', PORT))
    listener.listen(1)
    listener.settimeout(30)
    state = {}

    def serve():
        try:
            sock, _ = listener.accept()
            sock.settimeout(15)
            peer = rp.DaemonReceiver(
                sock, greeting_version=29, protocol=29,
            )
            peer.handshake(seed=0x12345678)

            # Protocol 29 daemon argv is newline-framed and blank-terminated.
            while peer._readline():
                pass

            parent_flags = rp.XMIT_TOP_DIR if forged_top_dir else 0
            flist = (
                rp.FileEntry(
                    'dir', mode=rp.S_IFDIR | 0o755,
                    extra_flags=parent_flags, protocol=29,
                ).encode()
                + rp.FileEntry(
                    'dir/file', mode=rp.S_IFREG | 0o644,
                    length=0, protocol=29,
                ).encode()
                + rp.end_of_flist(0, 29)
                + rp.w_int(0)  # protocol-29 post-flist io_error
            )
            # Deletion runs from the received flist before transfer data is
            # needed.  End the remaining phases cleanly enough for the client
            # to finish or report an ordinary stream error.
            peer.send_data(flist + rp.w_int(rp.NDX_DONE) * 4)
            peer.drain(timeout=3)
            peer.close()
        except Exception as exc:  # noqa: BLE001
            state['err'] = repr(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        proc = subprocess.run(
            rsync_argv(
                '-rR', '--delete',
                f'rsync://127.0.0.1:{PORT}/mod/dir/file', str(dest) + '/',
            ),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=20,
        )
    finally:
        thread.join(timeout=8)
        listener.close()
    return sentinel.exists(), proc, state


control_survived, control_proc, control_state = pull('control', False)
attack_survived, attack_proc, attack_state = pull('attack', True)

if not control_survived:
    test_fail(
        'protocol-29 control without XMIT_TOP_DIR deleted the sibling: '
        f'rc={control_proc.returncode}, server={control_state!r}, '
        f'output={control_proc.stdout}{control_proc.stderr}')

if not attack_survived:
    test_fail(
        'malicious protocol-29 sender marked implied parent dir as '
        'XMIT_TOP_DIR and expanded scoped --delete from dir/file to dir/, '
        'deleting receiver-owned must-survive; the protocol-30 implied-parent '
        'downgrade was not applied to the legacy branch; '
        f'control rc={control_proc.returncode}, attack rc={attack_proc.returncode}, '
        f'control server={control_state!r}, attack server={attack_state!r}, '
        f'attack output={attack_proc.stdout}{attack_proc.stderr}')

if attack_state.get('err') and control_state.get('err') != attack_state.get('err'):
    test_fail(
        f'attack server failed differently from control: attack={attack_state!r}, '
        f'control={control_state!r}, rc={attack_proc.returncode}, '
        f'output={attack_proc.stdout}{attack_proc.stderr}')

print('peer-legacy-implied-delete-scope: protocol-29 implied parent remained '
      'non-content under forged XMIT_TOP_DIR')
