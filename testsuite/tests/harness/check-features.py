#!/usr/bin/env python3

import json
import shlex
import subprocess
import sys

from harness import TestContext, requires, run
from harness.rsync import rsync_command_binary, test_fail

@requires(transports={'pipe'}, mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    tool = context.repository / 'testsuite/tools/check-features.py'
    if not tool.is_file():
        test_fail('the build feature check is missing')

    def check(binary, features, success):
        result = subprocess.run([sys.executable, str(tool), str(binary), *features],
                                capture_output=True, text=True, timeout=45)
        if (result.returncode == 0) != success:
            test_fail(f'wrong feature check result: {result.stdout}{result.stderr}')
        return result

    binary = rsync_command_binary()
    version = json.loads(subprocess.check_output([binary, '-VV'], text=True, timeout=30))
    features = {key for key, value in version['capabilities'].items() if value is True}
    features.update(version['checksum_list'])
    features.update(version['compress_list'])
    result = check(binary, sorted(features), True)
    if json.loads(result.stdout) != version:
        test_fail('the feature check changed the build metadata')
    result = check(binary, ['missing-feature'], False)
    if 'missing-feature' not in result.stderr or json.loads(result.stdout) != version:
        test_fail('a missing feature lost its diagnostic or build metadata')

    fake = context.scratch / 'rsync'
    version = {'capabilities': {'enabled': True, 'disabled': False, 'bits': 64},
               'checksum_list': ['checksum'], 'compress_list': ['compression']}
    fake.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote(json.dumps(version)) + '\n')
    fake.chmod(0o755)
    check(fake, ['enabled', 'checksum', 'compression'], True)
    for feature in ('disabled', 'bits', 'missing-feature'):
        check(fake, [feature], False)
    for field in ('checksum_list', 'compress_list'):
        feature = version[field][0]
        malformed = {**version, field: {feature: False}}
        fake.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote(json.dumps(malformed)) + '\n')
        check(fake, [feature], False)
    fake.write_text('#!/bin/sh\nprintf "%s\\n" not-json\n')
    check(fake, ['enabled'], False)
    fake.write_text('#!/bin/sh\nexit 7\n')
    check(fake, ['enabled'], False)

if __name__ == '__main__':
    run(test)
