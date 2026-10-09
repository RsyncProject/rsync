#!/usr/bin/env python3
"""Runner receipt contract"""

import json
import os
import subprocess
import sys
from pathlib import Path

from harness import Exit, TestContext, parse_peer_banner, requires, run
from rsyncfns import RSYNC, split_rsync_cmd, test_fail


@requires(features={'remote-shell'}, protocols={27, 28, 29, 30, 31, 32, 33}, transports={'pipe'}, min_peer='2.6.0', mutates={'filesystem', 'process'}, tags={'harness'})
def test(context: TestContext):
    receipt = context.scratch / 'run.json'
    env = os.environ.copy()
    env['scratchbase'] = str(context.scratch / 'nested')
    binary = next(part for part in split_rsync_cmd(RSYNC) if Path(part).is_file())
    banner = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True).stdout
    peer, protocol = parse_peer_banner(banner)
    profile = context.scratch / f'peer-{peer}.json'
    command = [sys.executable, str(context.repository / 'testsuite' / 'runtests.py'), 'smoke',
               f'--rsync-bin={binary}', f'--rsync-bin2={binary}', f'--tooldir={context.tools}',
               f'--srcdir={context.repository}', f'--profiles={profile}', f'--receipt={receipt}']
    profile.write_text(json.dumps({'peer': peer, 'protocol': protocol + 1}))
    mismatch = subprocess.run(command, env=env, capture_output=True, text=True)
    if mismatch.returncode != Exit.ERROR or 'profile protocol' not in mismatch.stderr:
        test_fail('peer protocol mismatch was not rejected')
    profile.write_text(json.dumps({'peer': peer, 'protocol': protocol,
                                   'xfail': {'alt-dest': 'test-fixture'}}))
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if result.returncode:
        test_fail(f'nested runner failed: {result.stdout}{result.stderr}')
    data = json.loads(receipt.read_text())
    if (data['summary']['exit_code'] or data['run']['selected'] != ['smoke']
            or data['run']['peer_version'] != peer or data['run']['protocol'] != protocol
            or data['run']['profiles'] != [f'peer-{peer}']
            or data['summary']['verdicts'] != {'pass': 1}):
        test_fail('receipt summary or selection is wrong')
    if (data['tests'][0]['name'] != 'smoke' or data['tests'][0]['outcome'] != 'pass'
            or data['tests'][0]['expected'] != 'pass'):
        test_fail('receipt test result is wrong')


if __name__ == '__main__':
    run(test)
