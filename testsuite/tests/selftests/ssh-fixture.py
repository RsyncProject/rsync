#!/usr/bin/env python3

import ast
import subprocess
import sys

from harness import TestContext, requires, run
from harness.ssh import SSHServer, command, config_path, write_rrsync


@requires(transports={'pipe', 'tcp'}, tags={'harness'})
def test(context: TestContext):
    template, output = context.scratch / 'template', context.scratch / 'configured'
    template.write_text("RSYNC = '/usr/bin/rsync'\n")
    binary = context.scratch / "binary with 'quotes'"
    write_rrsync(template, output, binary)
    value = ast.literal_eval(ast.parse(output.read_text()).body[0].value)
    if value != str(binary) or output.stat().st_mode & 0o777 != 0o755:
        raise ValueError('Restricted-shell configuration changed the binary path or permissions')
    for text in ('', 'RSYNC = 1\nRSYNC = 2\n'):
        template.write_text(text)
        try:
            write_rrsync(template, output, binary)
        except ValueError:
            continue
        raise ValueError('Invalid restricted-shell template was accepted')
    for path in ('bad\npath', 'bad\rpath', 'bad\0path', 'bad%path'):
        try:
            config_path(path)
        except ValueError:
            continue
        raise ValueError('Unsafe SSH configuration path was accepted')
    for address, port in (('0.0.0.0', 0), ('8.8.8.8', 0), ('127.0.0.1', True), ('127.0.0.1', 22)):
        try:
            SSHServer(context.scratch / 'unused', context.scratch, binary, template, address, port)
        except ValueError:
            continue
        raise ValueError('Invalid SSH listener was accepted')
    try:
        command([sys.executable, '-S', '-c', 'import time; time.sleep(30)'], {}, timeout=0.1)
    except subprocess.TimeoutExpired:
        return
    raise ValueError('SSH command deadline was not enforced')


if __name__ == '__main__':
    run(test)
