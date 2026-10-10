#!/usr/bin/env python3

import json

from harness import (TestContext, applies_to_peer, discover_tests, load_profile, merge_profiles,
                     parse_peer_banner, read_requirements, requires, run, test_name)
from rsyncfns import test_fail


@requires(protocols={27, 28, 29, 30, 31, 32, 33}, transports={'pipe'}, min_peer='2.6.0', tags={'harness'})
def test(context: TestContext):
    test_paths = discover_tests(context.repository / 'testsuite' / 'tests')
    requirements = {test_name(path): read_requirements(path) for path in test_paths}
    tests = set(requirements)
    paths = sorted((context.repository / 'testsuite' / 'profiles').glob('*.json'))
    profiles = [load_profile(path, tests) for path in paths]
    if not any(profile.peer for profile in profiles) or not any(profile.unsupported for profile in profiles):
        test_fail('profile set is incomplete')
    features = {feature for values in requirements.values() if values for feature in values['features']}
    for profile in profiles:
        unknown = set(profile.unsupported) - features
        if unknown:
            test_fail(f'{profile.name} permits unknown capabilities: {", ".join(sorted(unknown))}')
    peers = {profile.peer for profile in profiles if profile.peer}
    archived = {path.name[len('rsync_'):] for path in (context.repository / 'old_versions').glob('rsync_*')}
    if archived and peers != archived:
        test_fail('peer profiles do not match archived binaries')
    by_name = {profile.name: profile for profile in profiles}
    environment = [by_name[name] for name in ('linux', 'non-asan', 'pipe', 'root', 'self-peer')]
    peer, protocol, unsupported, xfail = merge_profiles(environment)
    if peer or protocol or not unsupported or xfail:
        test_fail('platform profile composition failed')
    for profile in profiles:
        if profile.peer and any(not applies_to_peer(requirements[name], profile.peer, profile.protocol)
                                for name in profile.xfail):
            test_fail(f'{profile.name} has an inapplicable deviation')
    if parse_peer_banner('rsync  version 3.5.1-gabc  protocol version 33') != ('3.5.1', 33):
        test_fail('version banner was not parsed')

    def reject(name, data):
        path = context.scratch / name
        path.write_text(json.dumps(data))
        try:
            load_profile(path, tests)
        except ValueError:
            return
        test_fail(f'{name} was accepted')

    reject('field.json', {'unknown': {}})
    reject('object.json', [])
    reject('peer.json', {'peer': ''})
    reject('peer-format.json', {'peer': 'current', 'protocol': 33})
    reject('missing-protocol.json', {'peer': '3.4.1'})
    reject('wrong-name.json', {'peer': '3.4.1', 'protocol': 32})
    reject('protocol.json', {'protocol': True})
    reject('unsupported.json', {'unsupported': {'acl': ''}})
    reject('xfail.json', {'xfail': {'missing-test': 'issue-1'}})


if __name__ == '__main__':
    run(test)
