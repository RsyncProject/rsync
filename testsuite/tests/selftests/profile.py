#!/usr/bin/env python3

import json

from harness import TestContext, requires, run
from harness.metadata import applies_to_peer, discover_tests, read_requirements, test_name
from harness.profile import load_profile, merge_profiles, parse_peer_banner
from harness.results import test_fail
from harness.process import file_digest
from harness.profile import load_binaries

@requires(transports={'pipe'}, tags={'harness'})
def test(context: TestContext):
    test_paths = discover_tests(context.repository / 'testsuite' / 'tests')
    requirements = {test_name(path): read_requirements(path) for path in test_paths}
    tests = set(requirements)
    paths = sorted((context.repository / 'testsuite' / 'profiles').glob('*.json'))
    profiles = [load_profile(path, tests) for path in paths]
    if not any(profile.peer for profile in profiles) or not any(profile.unsupported for profile in profiles):
        test_fail('profile set is incomplete')
    features = {feature for values in requirements.values() if values for feature in values['features']}
    structural = {'asan', 'legacy_peer', 'nonroot', 'protocol_30', 'protocol_33', 'root', 'tcp'}
    for profile in profiles:
        unknown = (set(profile.not_applicable) | set(profile.unsupported)) - features
        if unknown:
            test_fail(f'{profile.name} permits unknown capabilities: {", ".join(sorted(unknown))}')
        misplaced = set(profile.unsupported) & structural
        if misplaced:
            test_fail(f'{profile.name} treats structural requirements as unsupported: '
                      f'{", ".join(sorted(misplaced))}')
        merge_profiles([profile])
    peers = {profile.peer for profile in profiles if profile.peer}
    archive = context.repository / 'old_versions'
    archived = {path.name[len('rsync_'):] for path in archive.glob('rsync_*')}
    if archived or (archive / 'binaries.json').exists():
        records = load_binaries(archive)
        recipe = file_digest(archive / 'build_static.sh')
        if any(record['recipe'] != recipe for record in records.values()):
            test_fail('archived binaries use a different build recipe')
    if archived and peers != archived:
        test_fail('peer profiles do not match archived binaries')
    by_name = {profile.name: profile for profile in profiles}
    environment = [by_name[name] for name in ('linux', 'non-asan', 'pipe', 'root', 'self-peer')]
    peer, protocol, not_applicable, unsupported, xfail = merge_profiles(environment)
    expected_n_a = {'asan', 'darwin', 'darwin_interpose', 'legacy_peer', 'nonroot', 'tcp'}
    if peer or protocol or set(not_applicable) != expected_n_a or not unsupported or xfail:
        test_fail('platform profile composition failed')
    _, _, not_applicable, unsupported, _ = merge_profiles([by_name['nonroot'], by_name['cygwin']])
    if 'device_nodes' not in not_applicable or 'device_nodes' in unsupported:
        test_fail('N/A did not take precedence over unsupported')
    for profile in profiles:
        if profile.peer and any(not applies_to_peer(requirements[name], profile.peer, profile.protocol)
                                for name in profile.xfail):
            test_fail(f'{profile.name} has an inapplicable deviation')
    if parse_peer_banner('rsync  version 3.5.1-gabc  protocol version 33') != ('3.5.1', 33):
        test_fail('version banner was not parsed')

    n_a_path = context.scratch / 'n-a.json'
    n_a_path.write_text(json.dumps({'not_applicable': {'tcp': 'pipe transport'}}))
    n_a = load_profile(n_a_path, tests)
    if n_a.not_applicable != {'tcp': 'pipe transport'}:
        test_fail('N/A requirements were not loaded')
    conflict_path = context.scratch / 'conflict.json'
    conflict_path.write_text(json.dumps({'not_applicable': {'tcp': 'different reason'}}))
    combined = merge_profiles([n_a, load_profile(conflict_path, tests)])[2]
    if combined['tcp'] != ('different reason', 'pipe transport'):
        test_fail('composed N/A reasons were lost')

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
    reject('not-applicable.json', {'not_applicable': {'tcp': ''}})
    reject('unsupported.json', {'unsupported': {'acl': ''}})
    reject('xfail.json', {'xfail': {'missing-test': 'issue-1'}})

    duplicate = context.scratch / 'duplicate.json'
    duplicate.write_text('{"unsupported": {}, "unsupported": {}}')
    try:
        load_profile(duplicate, tests)
    except ValueError:
        pass
    else:
        test_fail('a duplicate profile field was accepted')

    archive = context.scratch / 'binaries'
    archive.mkdir()
    binary = archive / 'rsync_3.2.7'
    binary.write_bytes(b'qualified fixture')
    manifest = archive / 'binaries.json'
    record = {'source': 'a' * 40, 'protocol': 31, 'sha256': file_digest(binary), 'recipe': 'b' * 64,
              'compiler': 'test compiler', 'target': 'test target', 'libc': 'test libc'}
    manifest.write_text(json.dumps({binary.name: record}))
    if load_binaries(archive) != {binary.name: record}:
        test_fail('a valid binary manifest changed while loading')

    def reject_binary(data):
        manifest.write_text(json.dumps(data))
        try:
            load_binaries(archive)
        except ValueError:
            return
        test_fail(f'invalid binary manifest was accepted: {data}')

    reject_binary([])
    reject_binary({})
    for field, value in (('source', 'g' * 40), ('sha256', '0'), ('recipe', ''),
                         ('protocol', True), ('compiler', ''), ('unknown', True)):
        reject_binary({binary.name: {**record, field: value}})
    extra = archive / 'rsync_9.9.9'
    extra.touch()
    reject_binary({binary.name: record})
    extra.unlink()
    binary.write_bytes(b'changed fixture')
    reject_binary({binary.name: record})
    binary.write_bytes(b'qualified fixture')
    saved = archive / 'saved'
    binary.rename(saved)
    binary.symlink_to(saved.name)
    reject_binary({binary.name: record})
    binary.unlink()
    saved.rename(binary)
    manifest.write_text('{"rsync_3.2.7": {}, "rsync_3.2.7": {}}')
    try:
        load_binaries(archive)
    except ValueError:
        pass
    else:
        test_fail('a duplicate archived peer was accepted')

if __name__ == '__main__':
    run(test)
