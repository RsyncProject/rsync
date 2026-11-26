#!/usr/bin/env python3

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def check(binary, pam):
    with tempfile.TemporaryDirectory(prefix='rsync-pam-') as temporary:
        base = Path(temporary)
        src, dest = base / 'source', base / 'destination'
        src.mkdir()
        dest.mkdir()
        (src / 'file').write_bytes(b'PAM account test\n')
        users = ['allowed', 'denied', 'expired', 'interactive']
        secrets = base / 'secrets'
        secrets.write_text(''.join(f'{user}:test-secret\n' for user in users))
        secrets.chmod(0o600)
        config = base / 'rsyncd.conf'
        identities = f'uid = {os.getuid()}\ngid = {os.getgid()}\n' if os.geteuid() == 0 else ''
        config.write_text(
            f'use chroot = no\n{identities}'
            f'log file = {base / "rsyncd.log"}\n'
            f'[normal]\npath = {dest}\nread only = no\nauth users = {", ".join(users)}\n'
            f'secrets file = {secrets}\n'
            f'[account]\npath = {dest}\nread only = no\nauth users = {", ".join(users)}\n'
            f'secrets file = {secrets}\nuse pam = yes\n'
            f'[anonymous]\npath = {dest}\nread only = no\nuse pam = yes\n')
        calls = base / 'pam-calls'
        calls.touch()
        daemon = ['env']
        if pam is not None:
            wrapper, module = pam
            policies = base / 'pam.d'
            policies.mkdir()
            (policies / 'rsync').write_text(f'account required {module}\n')
            daemon.extend([f'LD_PRELOAD={wrapper}', 'PAM_WRAPPER=1',
                           f'PAM_WRAPPER_SERVICE_DIR={policies}', f'RSYNC_TEST_PAM_CALLS={calls}'])
        daemon.extend([str(binary), f'--config={config}', '--daemon'])
        connector = ' '.join(shlex.quote(arg) for arg in daemon)

        def copy(label, module, user, password, status, expected_calls):
            payload = dest / 'file'
            if payload.exists():
                payload.unlink()
            authority = f'{user}@localhost' if user else 'localhost'
            result = subprocess.run(
                [str(binary), '-r', f'{src}/', f'rsync://{authority}/{module}/'],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
                env={**os.environ, 'RSYNC_CONNECT_PROG': connector, 'RSYNC_PASSWORD': password})
            if result.returncode != status:
                raise ValueError(f'{label}: expected exit {status}, got {result.returncode}: {result.stderr}')
            if payload.exists() != (status == 0):
                raise ValueError(f'{label}: file delivery did not match authorisation')
            if status == 0 and payload.read_bytes() != (src / 'file').read_bytes():
                raise ValueError(f'{label}: transferred contents differ')
            if calls.read_text().splitlines() != expected_calls:
                raise ValueError(f'{label}: unexpected PAM calls')

        copy('ordinary auth', 'normal', 'allowed', 'test-secret', 0, [])
        copy('anonymous configuration refused', 'anonymous', '', '', 5, [])
        if pam is None:
            copy('PAM unavailable', 'account', 'allowed', 'test-secret', 5, [])
            return
        copy('wrong secret', 'account', 'allowed', 'wrong-secret', 5, [])
        copy('account allowed', 'account', 'allowed', 'test-secret', 0, ['allowed'])
        copy('account denied', 'account', 'denied', 'test-secret', 5, ['allowed', 'denied'])
        copy('account expired', 'account', 'expired', 'test-secret', 5, ['allowed', 'denied', 'expired'])
        copy('prompt refused', 'account', 'interactive', 'test-secret', 5, users)


def main():
    parser = argparse.ArgumentParser(description='Check rsync daemon PAM account policy in isolation.')
    parser.add_argument('--rsync', required=True, type=Path)
    parser.add_argument('--with-pam', nargs=2, type=Path, metavar=('WRAPPER', 'MODULE'))
    args = parser.parse_args()
    pam = [path.resolve(strict=True) for path in args.with_pam] if args.with_pam else None
    check(args.rsync.resolve(strict=True), pam)
    print('PAM account policy verified' if pam else 'PAM-disabled build verified')


if __name__ == '__main__':
    main()
