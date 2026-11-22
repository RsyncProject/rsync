#!/usr/bin/env python3

from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import sys
import tempfile

from harness.process import quote_command
from harness import TestContext, requires, run, environment
from harness.comparison import checkit
from harness.filesystem import make_data_file, makepath
from harness.results import test_error
from harness.rsync import rsync_argv_for, rsync_command_binary

@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'transfer'})
def test(context: TestContext):
    original = environment.RSYNC
    with ExitStack() as cleanup:
        base = context.scratch
        prefix = []
        if os.geteuid() == 0 and sys.platform == 'linux':
            setpriv = shutil.which('setpriv')
            if not setpriv:
                test_error('setpriv is required to enforce read-only files as root')
            base = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='rsync-partial-', dir='/tmp')))
            binary = base / 'rsync'
            shutil.copy2(rsync_command_binary(), binary)
            prefix = [setpriv, '--inh-caps', '-all', '--bounding-set', '-all']

        source, destination = base / 'from', base / 'to'
        makepath(source, destination / '.~tmp~')
        (destination / '.~tmp~').chmod(0o700)
        make_data_file(source / 'some_file', 1024 * 1024)
        (source / 'some_file').chmod(0o444)
        partial = destination / '.~tmp~' / 'some_file'
        shutil.copy2(source / 'some_file', partial)

        if prefix:
            probe = ('if ! test -f "$1" || ! test -r "$1"; then\n'
                     '    echo "read-only partial fixture is not enforced" >&2\n'
                     '    exit 1\n'
                     'fi\n'
                     'if (printf x >>"$1") 2>/dev/null; then\n'
                     '    echo "read-only partial fixture is not enforced" >&2\n'
                     '    exit 1\n'
                     'fi\n'
                     'shift\n'
                     'exec "$@"')
            environment.RSYNC = quote_command([*prefix, 'sh', '-c', probe, 'sh', str(partial), *rsync_argv_for(binary)])
        previous = os.getcwd()
        try:
            os.chdir(base)
            checkit(['-avv', '--partial', '--delay-updates', f'{source}/', f'{destination}/'], source, destination)
        finally:
            os.chdir(previous)
            environment.RSYNC = original

if __name__ == '__main__':
    run(test)
