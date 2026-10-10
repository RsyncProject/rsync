#!/usr/bin/env python3
"""Test metadata schema"""

from harness import TestContext, applies_to_peer, metadata, placeholder_target, read_requirements, requires, resolve_test_path, run
from rsyncfns import test_fail


@requires(protocols={27, 28, 29, 30, 31, 32, 33}, transports={'pipe'}, min_peer='2.6.0', tags={'harness'})
def test(context: TestContext):
    metadata = read_requirements(context.repository / 'testsuite' / 'tests' / 'smoke_test.py')
    if metadata['min_peer'] != '2.6.0' or metadata['transports'] != ('pipe', 'tcp'):
        test_fail('smoke metadata was not discovered')
    if not applies_to_peer(metadata, '2.6.0', 27, 'pipe'):
        test_fail('matching peer metadata was rejected')
    if (applies_to_peer(metadata, '2.5.0', 27, 'pipe')
            or applies_to_peer(metadata, '2.6.0', 26, 'pipe')
            or applies_to_peer(metadata, '2.6.0', 27, 'socket')):
        test_fail('inapplicable peer metadata was accepted')

    module = context.scratch / 'module_test.py'
    module.write_text("from harness import metadata\nmetadata(features={'acl'}, tags={'version-mix'})\n")
    if read_requirements(module)['features'] != ('acl',):
        test_fail('module metadata was not discovered')

    alias = context.scratch / 'alias_test.py'
    alias.symlink_to(module.name)
    if resolve_test_path(alias) != module or read_requirements(alias) != read_requirements(module):
        test_fail('link alias did not inherit target metadata')

    placeholder = context.scratch / 'placeholder_test.py'
    placeholder.write_text(module.name)
    if (placeholder_target(placeholder) != module or resolve_test_path(placeholder) != module
            or read_requirements(placeholder) != read_requirements(module)):
        test_fail('placeholder did not inherit target metadata')

    cycle_a = context.scratch / 'cycle-a_test.py'
    cycle_b = context.scratch / 'cycle-b_test.py'
    cycle_a.write_text(cycle_b.name)
    cycle_b.write_text(cycle_a.name)
    try:
        read_requirements(cycle_a)
    except ValueError:
        pass
    else:
        test_fail('placeholder cycle was accepted')

    inferred = context.scratch / 'inferred_test.py'
    inferred.write_text("require_tcp('tcp')\nrequire_asan('asan')\n"
                        "setup_chroot_inner('chroot')\n"
                        "test_skipped('root', capability='nonroot')\n")
    if read_requirements(inferred)['features'] != ('asan', 'chroot', 'nonroot', 'root', 'tcp'):
        test_fail('capability metadata was not inferred')

    invalid_capability = context.scratch / 'invalid_capability_test.py'
    invalid_capability.write_text("test_skipped('bad', capability=1)\n")
    try:
        read_requirements(invalid_capability)
    except ValueError:
        pass
    else:
        test_fail('invalid capability metadata was accepted')

    invalid = (
        {'features': {''}}, {'features': 'acl'}, {'features': {'acl', 1}},
        {'protocols': {0}}, {'protocols': {True}},
        {'transports': {'socket'}}, {'min_peer': 'three'}, {'min_peer': True},
        {'root': 1}, {'parallel': 1}, {'cost': 'slow'},
    )
    for values in invalid:
        try:
            requires(**values)
        except ValueError:
            continue
        test_fail(f'invalid metadata was accepted: {values}')
    try:
        metadata(unknown=True)
    except TypeError:
        pass
    else:
        test_fail('unknown metadata field was accepted')


if __name__ == '__main__':
    run(test)
