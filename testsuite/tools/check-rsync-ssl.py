#!/usr/bin/env python3

import argparse
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


class CheckError(RuntimeError):
    pass


def run_openssl(command, **kwargs):
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=30,
                                **kwargs)
    except subprocess.TimeoutExpired as error:
        raise CheckError(f'{command[0]} timed out') from error
    if result.returncode != 0:
        output = '\n'.join(result.stdout.splitlines()[-20:])
        raise CheckError(f'{command[0]} exited {result.returncode}:\n{output}')
    return result


def generate_certificates(openssl, base):
    ca_key = base / 'ca.key'
    ca_cert = base / 'ca.pem'
    wrong_ca_key = base / 'wrong-ca.key'
    wrong_ca_cert = base / 'wrong-ca.pem'

    for name, key, cert in (
        ('rsync TLS test CA', ca_key, ca_cert),
        ('untrusted rsync TLS test CA', wrong_ca_key, wrong_ca_cert),
    ):
        run_openssl([
            openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-sha256', '-days', '1', '-subj', f'/CN={name}',
            '-addext', 'basicConstraints=critical,CA:TRUE',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
            '-keyout', str(key), '-out', str(cert),
        ])

    certificates = {}
    for serial, (name, dns_name) in enumerate((('good', 'localhost'),
                                                ('wrong-host', 'wrong.invalid')), 2):
        key = base / f'{name}.key'
        request = base / f'{name}.csr'
        cert = base / f'{name}.pem'
        extensions = base / f'{name}.ext'
        extensions.write_text(
            '[server]\n'
            'basicConstraints=critical,CA:FALSE\n'
            'keyUsage=critical,digitalSignature,keyEncipherment\n'
            'extendedKeyUsage=serverAuth\n'
            f'subjectAltName=DNS:{dns_name}\n',
            encoding='ascii',
        )
        run_openssl([
            openssl, 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
            '-sha256', '-subj', f'/CN={dns_name}',
            '-keyout', str(key), '-out', str(request),
        ])
        run_openssl([
            openssl, 'x509', '-req', '-sha256', '-days', '1',
            '-in', str(request), '-CA', str(ca_cert), '-CAkey', str(ca_key),
            '-set_serial', str(serial), '-extfile', str(extensions),
            '-extensions', 'server', '-out', str(cert),
        ])
        certificates[name] = (cert, key)

    return ca_cert, wrong_ca_cert, certificates


def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait_for_listener(process, port):
    deadline = time.monotonic() + 10
    failure = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError as error:
                if error.errno == errno.EADDRINUSE:
                    return
                failure = error
        time.sleep(0.05)
    output = process.stderr.read() if process.poll() is not None else ''
    raise CheckError(f'TLS server did not bind 127.0.0.1:{port}: {failure}\n{output}')


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=5)
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def create_module(base):
    module = base / 'module'
    module.mkdir()
    payload = 'local rsync TLS fixture\n'
    (module / 'payload.txt').write_text(payload, encoding='ascii')
    config = base / 'rsyncd.conf'
    log_file = base / 'rsyncd.log'
    identity = ''
    if os.geteuid() == 0:
        identity = f'uid = {os.geteuid()}\ngid = {os.getegid()}\n'
    config.write_text(
        'use chroot = no\n'
        f'{identity}'
        f'pid file = {base / "rsyncd.pid"}\n'
        f'log file = {log_file}\n'
        '[fixture]\n'
        f'path = {module}\n'
        'read only = yes\n',
        encoding='utf-8',
    )
    return config, payload


@contextmanager
def tls_server(rsync, openssl, config, certificate, key):
    port = unused_port()
    rsync_input, tls_output = os.pipe()
    tls_input, rsync_output = os.pipe()
    rsync_process = None
    tls_process = None
    try:
        rsync_process = subprocess.Popen(
            [rsync, '--server', '--daemon', f'--config={config}', '.'],
            stdin=rsync_input, stdout=rsync_output, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        tls_process = subprocess.Popen(
            [openssl, 's_server', '-accept', f'127.0.0.1:{port}',
             '-cert', str(certificate), '-key', str(key), '-quiet',
             '-no_ign_eof', '-naccept', '1'],
            stdin=tls_input, stdout=tls_output, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        for descriptor in (rsync_input, tls_output, tls_input, rsync_output):
            os.close(descriptor)
        wait_for_listener(tls_process, port)
        yield port, rsync_process, tls_process
    finally:
        for descriptor in (rsync_input, tls_output, tls_input, rsync_output):
            try:
                os.close(descriptor)
            except OSError:
                pass
        for process in (tls_process, rsync_process):
            if process:
                stop_process(process)
                process.stderr.close()


def transfer(rsync, rsync_ssl, openssl, base, config, certificate, key,
             ca_cert, payload, label, expect_success, skip_hostname=False,
             failure_text=None):
    destination = base / label
    destination.mkdir()
    environment = os.environ.copy()
    for name in (
        'OPENSSL_CONF', 'RSYNC_CONNECT_PROG', 'RSYNC_PASSWORD', 'RSYNC_PORT',
        'RSYNC_PROXY', 'RSYNC_RSH', 'RSYNC_SSL_PORT', 'SSL_CERT_DIR',
        'SSL_CERT_FILE',
        'RSYNC_SSL_ALLOW_INSECURE_GNUTLS', 'RSYNC_SSL_ALLOW_INSECURE_STUNNEL',
        'RSYNC_SSL_CA_CERT', 'RSYNC_SSL_CERT', 'RSYNC_SSL_GNUTLS',
        'RSYNC_SSL_KEY', 'RSYNC_SSL_OPENSSL', 'RSYNC_SSL_SKIP_HOSTNAME_CHECK',
        'RSYNC_SSL_STUNNEL', 'RSYNC_SSL_TYPE',
    ):
        environment.pop(name, None)
    environment.update({
        'PATH': str(Path(rsync).parent) + os.pathsep + environment.get('PATH', ''),
        'RSYNC_SSL_CA_CERT': str(ca_cert),
        'RSYNC_SSL_OPENSSL': openssl,
        'RSYNC_SSL_TYPE': 'openssl',
    })
    if skip_hostname:
        environment['RSYNC_SSL_SKIP_HOSTNAME_CHECK'] = '1'
    with tls_server(rsync, openssl, config, certificate, key) as server:
        port, rsync_process, tls_process = server
        command = [
            rsync_ssl, '--type=openssl', '--no-motd', '--contimeout=5',
            '--timeout=10', f'--port={port}',
            'localhost::fixture/payload.txt', str(destination) + os.sep,
        ]
        with subprocess.Popen(
            command, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            start_new_session=True) as client:
            try:
                output, _ = client.communicate(timeout=20)
            except subprocess.TimeoutExpired as error:
                stop_process(client)
                client.communicate()
                raise CheckError(f'{label}: transfer timed out') from error
            status = client.returncode
        for name, process in (('OpenSSL', tls_process), ('rsync', rsync_process)):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                raise CheckError(f'{label}: {name} server did not exit') from error
        server_status = (tls_process.returncode, rsync_process.returncode)
        server_output = '\n'.join(
            output for output in (
                tls_process.stderr.read(), rsync_process.stderr.read()) if output)
    received = destination / 'payload.txt'
    if expect_success:
        content = received.read_text(encoding='ascii') if received.is_file() else None
        if status != 0 or content != payload or server_status != (0, 0):
            raise CheckError(
                f'{label}: expected success, got {status} and '
                f'server status {server_status} and content {content!r}\n'
                f'{output}{server_output}'
            )
    elif status == 0 or any(destination.iterdir()):
        raise CheckError(
            f'{label}: rejected certificate produced status {status} '
            f'or destination data\n{output}'
        )
    elif failure_text and failure_text.lower() not in output.lower():
        raise CheckError(
            f'{label}: transfer failed for the wrong reason\n{output}'
        )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rsync', required=True)
    parser.add_argument('--rsync-ssl', required=True)
    parser.add_argument('--openssl', default=shutil.which('openssl'))
    return parser.parse_args()


def main():
    args = parse_args()
    if os.name != 'posix':
        raise CheckError('the installed TLS check requires a POSIX host')
    for name, path in (('rsync', args.rsync), ('rsync-ssl', args.rsync_ssl),
                       ('openssl', args.openssl)):
        if not path or not Path(path).is_file() or not os.access(path, os.X_OK):
            raise CheckError(f'{name} is not an executable file: {path!r}')

    with tempfile.TemporaryDirectory(prefix='rsync-ssl-check-') as directory:
        base = Path(directory)
        ca_cert, wrong_ca_cert, certificates = generate_certificates(args.openssl, base)
        config, payload = create_module(base)
        good_cert, good_key = certificates['good']
        wrong_cert, wrong_key = certificates['wrong-host']
        transfer(args.rsync, args.rsync_ssl, args.openssl, base, config,
                 good_cert, good_key, ca_cert, payload, 'trusted', True)
        transfer(args.rsync, args.rsync_ssl, args.openssl, base, config,
                 good_cert, good_key, wrong_ca_cert, payload, 'untrusted-ca', False,
                 failure_text='certificate verify failed')
        transfer(args.rsync, args.rsync_ssl, args.openssl, base, config,
                 wrong_cert, wrong_key, ca_cert, payload, 'wrong-hostname', False,
                 failure_text='hostname mismatch')
        transfer(args.rsync, args.rsync_ssl, args.openssl, base, config,
                 wrong_cert, wrong_key, ca_cert, payload, 'hostname-opt-out', True,
                 skip_hostname=True)
        transfer(args.rsync, args.rsync_ssl, args.openssl, base, config,
                 wrong_cert, wrong_key, '', payload, 'verification-opt-out', True)

    print('rsync-ssl verified trusted, rejected and opt-out TLS paths')


if __name__ == '__main__':
    try:
        main()
    except CheckError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
