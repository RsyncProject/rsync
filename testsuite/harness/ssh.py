import ipaddress
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socketserver
import subprocess
import sys
import threading

from .process import quote_command


def write_rrsync(source, destination, binary):
    text, count = re.subn(r'(?m)^RSYNC\s*=.*$', lambda _: f'RSYNC = {str(binary)!r}', Path(source).read_text())
    if count != 1:
        raise ValueError('Restricted-shell template has no unique rsync assignment')
    Path(destination).write_text(text)
    Path(destination).chmod(0o755)
    return Path(destination)


def command(argv, env, timeout=30):
    with subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True) as child:
        try:
            stdout, stderr = child.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate(timeout=5)
            raise
        return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


def config_path(path):
    value = str(path)
    if any(char in value for char in '\r\n\0%'):
        raise ValueError('Unsupported SSH fixture path')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


class SSHServer:
    def __init__(self, directory, restricted, binary, rrsync, address='127.0.0.1', port=0):
        ip = ipaddress.ip_address(address)
        if (not isinstance(ip, ipaddress.IPv4Address) or ip.is_unspecified or ip.is_multicast or ip.is_link_local
                or not (ip.is_private or ip in ipaddress.ip_network('100.64.0.0/10'))):
            raise ValueError('SSH fixture requires an explicit private address')
        if type(port) is not int or port != 0 and not 1024 <= port <= 65535:
            raise ValueError('Invalid SSH fixture port')
        if os.geteuid() != 0:
            raise ValueError('SSH fixture setup requires root')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(mode=0o700)
        self.restricted = Path(restricted).resolve(strict=True)
        self.binary = Path(binary).resolve(strict=True)
        self.address = str(ip)
        self.user = pwd.getpwuid(os.geteuid()).pw_name
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*\$?', self.user):
            raise ValueError('Unsupported SSH fixture account name')
        self.env = {key: os.environ[key] for key in ('PATH', 'RUNNER_TRACKING_ID') if key in os.environ}
        self.env.update(HOME=str(self.directory), LC_ALL='C')
        self.ssh = shutil.which('ssh')
        self.sshd = shutil.which('sshd') or '/usr/sbin/sshd'
        if not self.ssh or not Path(self.sshd).is_file():
            raise ValueError('OpenSSH client and server are required')
        self.wrapper = write_rrsync(rrsync, self.directory / 'rrsync', self.binary)
        for name in ('host', 'client'):
            result = command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.directory / name)], self.env)
            if result.returncode:
                raise ValueError('SSH fixture key generation failed')
        (self.directory / 'authorized_keys').write_bytes((self.directory / 'client.pub').read_bytes())
        self.config = self.directory / 'sshd_config'
        forced = quote_command([sys.executable, '-S', str(self.wrapper), str(self.restricted)])
        self.config.write_text('\n'.join([
            'HostKey ' + config_path(self.directory / 'host'),
            'AuthorizedKeysFile ' + config_path(self.directory / 'authorized_keys'),
            'ForceCommand ' + forced,
            'AllowUsers ' + self.user,
            'AuthenticationMethods publickey', 'PubkeyAuthentication yes',
            'PasswordAuthentication no', 'KbdInteractiveAuthentication no', 'HostbasedAuthentication no',
            'PermitRootLogin prohibit-password', 'UsePAM yes', 'StrictModes yes',
            'DisableForwarding yes', 'X11Forwarding no',
            'PermitTunnel no', 'PermitTTY no', 'PermitUserEnvironment no', 'PermitUserRC no',
            'LoginGraceTime 10', 'ClientAliveInterval 5', 'ClientAliveCountMax 1', 'MaxSessions 1',
            'LogLevel ERROR', '',
        ]))
        result = command([self.sshd, '-t', '-f', str(self.config)], self.env)
        if result.returncode:
            raise ValueError('SSH fixture configuration failed: ' + result.stderr.decode(errors='replace').strip())
        self.active, self.lock = set(), threading.Lock()
        self.errors = []
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(handler):
                with (owner.directory / 'sshd.log').open('ab') as log:
                    with subprocess.Popen([owner.sshd, '-i', '-e', '-f', str(owner.config)], env=owner.env,
                                          stdin=handler.request, stdout=handler.request, stderr=log,
                                          start_new_session=True) as child:
                        with owner.lock:
                            owner.active.add(child)
                        try:
                            child.wait(timeout=45)
                        except subprocess.TimeoutExpired:
                            owner.errors.append('SSH session exceeded its deadline')
                            owner.stop(child)
                        finally:
                            with owner.lock:
                                owner.active.discard(child)

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = False
            block_on_close = True

            def handle_error(server, request, client_address):
                owner.errors.append('SSH session handler failed')

        self.server = Server((self.address, port), Handler)
        try:
            self.port = self.server.server_address[1]
            host = (self.directory / 'host.pub').read_text().split()
            self.known_hosts = self.directory / 'known_hosts'
            self.known_hosts.write_text(f'[{self.address}]:{self.port} {host[0]} {host[1]}\n')
            self.thread = threading.Thread(target=self.server.serve_forever, kwargs=dict(poll_interval=0.1))
        except BaseException:
            self.server.server_close()
            raise

    @staticmethod
    def stop(child):
        if child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
        except ProcessLookupError:
            child.wait(timeout=5)

    def client(self, known_hosts=None):
        return [self.ssh, '-F', '/dev/null', '-p', str(self.port), '-i', str(self.directory / 'client'),
                '-oBatchMode=yes', '-oIdentitiesOnly=yes', '-oIdentityAgent=none',
                '-oStrictHostKeyChecking=yes', '-oGlobalKnownHostsFile=/dev/null',
                '-oUserKnownHostsFile=' + config_path(known_hosts or self.known_hosts),
                '-oPasswordAuthentication=no', '-oKbdInteractiveAuthentication=no',
                '-oClearAllForwardings=yes', '-oForwardAgent=no', '-oForwardX11=no',
                '-oConnectTimeout=5', '-oConnectionAttempts=1', '-oLogLevel=ERROR']

    def rsh(self, known_hosts=None):
        return quote_command(self.client(known_hosts))

    def remote(self, path):
        return f'{self.user}@{self.address}:{path}'

    def __enter__(self):
        try:
            self.thread.start()
        except BaseException:
            self.server.server_close()
            raise
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        with self.lock:
            active = list(self.active)
        for child in active:
            self.stop(child)
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive() or self.errors:
            raise ValueError('SSH fixture did not shut down cleanly')
