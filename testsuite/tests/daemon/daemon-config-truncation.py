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
                            while data := client.recv(4096):
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
            if process.returncode != 255 or b'valid' not in output:
                test_fail(f'valid config failed: {output!r} {error!r}')
        elif process.returncode != 1:
            test_fail(f'incomplete section: exit {process.returncode}: {output!r} {error!r}')


if __name__ == '__main__':
    run(test)
