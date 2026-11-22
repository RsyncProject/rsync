#!/usr/bin/env python3

import socket
import subprocess
from harness import TestContext, requires, run
from harness.results import test_fail
from harness.rsync import rsync_argv


@requires(transports={'tcp'}, mutates={'filesystem', 'process', 'socket'}, tags={'configuration', 'daemon'})
def test(context: TestContext):
    context.source.mkdir()
    config = context.scratch / 'rsyncd.conf'
    valid = f'use chroot = no\n[valid]\npath = {context.source}\n'
    for text in (valid, '[', '[unfinished', '[unfinished\n'):
        config.write_text(text)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            with socket.create_connection(listener.getsockname(), timeout=5) as client:
                connection, _ = listener.accept()
                with connection, subprocess.Popen(rsync_argv('--daemon', '--no-detach', f'--config={config}'),
                                                   stdin=connection, stdout=connection, stderr=subprocess.PIPE) as process:
                    connection.close()
                    chunks = []
                    try:
                        try:
                            client.sendall(b'@RSYNCD: 30.0\n\n')
                            while True:
                                data = client.recv(4096)
                                if not data:
                                    break
                                chunks.append(data)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        process.wait(timeout=5)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                    output = b''.join(chunks)
                    error = process.stderr.read()
        if text == valid:
            lines = output.splitlines()
            if (process.returncode != 0 or not lines or not lines[0].startswith(b'@RSYNCD: ')
                    or lines[-1] != b'@RSYNCD: EXIT'
                    or not any(line.split()[:1] == [b'valid'] for line in lines[1:-1])):
                test_fail(f'valid config: exit {process.returncode}: {output!r} {error!r}')
        elif process.returncode != 1:
            test_fail(f'incomplete section: exit {process.returncode}: {output!r} {error!r}')


if __name__ == '__main__':
    run(test)
