#!/usr/bin/env python3

import os
import subprocess

from harness import TestContext, requires, run
from harness.results import test_fail


@requires(transports={'pipe', 'tcp'}, mutates={'filesystem', 'process'}, tags={'build', 'regression'})
def test(context: TestContext):
    script = context.repository / 'install-sh'
    source = context.scratch / 'source file'
    contents = b'install fixture\n'
    source.write_bytes(contents)
    env = {'PATH': os.environ.get('PATH', os.defpath), 'LC_ALL': 'C',
           'transformarg': 's/^/inherited-/'}
    cases = (
        (['-t=-e s/^/pre-/ -e s/$/-post/', '-b=.bin'], 'pre-target-post.bin'),
        ([], 'target.bin'),
    )
    for options, expected_name in cases:
        destination = context.scratch / f'install {expected_name}' / 'target.bin'
        installed = destination.with_name(expected_name)
        result = subprocess.run(
            ['/bin/sh', str(script), '-c', *options, str(source), str(destination)],
            cwd=context.scratch, env=env, capture_output=True, text=True, timeout=10,
        )
        if result.returncode or not installed.is_file():
            test_fail(f'install-sh did not install {expected_name}: exit {result.returncode}\n'
                      f'{result.stdout}{result.stderr}')
        if installed.read_bytes() != contents or set(destination.parent.iterdir()) != {installed}:
            test_fail(f'install-sh did not install only {expected_name} with the expected contents')


if __name__ == '__main__':
    run(test)
