#!/usr/bin/env python3

from harness import (TestContext, applies_to_peer, discover_tests, metadata, placeholder_target,
                     read_requirements, requires, resolve_test_path, run, test_name)
from harness.rsync import test_fail

@requires(transports={'pipe'}, tags={'harness'})
def test(context: TestContext):
    discovery = context.scratch / 'discovery'
    first = discovery / 'transfer' / 'first.py'
    second = discovery / 'daemon' / 'second.py'
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text('')
    second.write_text('')
    paths = discover_tests(discovery)
    if paths != [first, second] or [test_name(path) for path in paths] != ['first', 'second']:
        test_fail('nested tests were not discovered in basename order')
    duplicate = discovery / 'build' / 'first.py'
    duplicate.parent.mkdir()
    duplicate.write_text('')
    try:
        discover_tests(discovery)
    except ValueError:
        pass
    else:
        test_fail('duplicate test names were accepted')

    decorated = context.scratch / 'decorated.py'
    decorated.write_text("from harness import requires\n"
                         "@requires(protocols={27}, transports={'pipe', 'tcp'}, min_peer='2.6.0', "
                         "tags={'version-mix'})\n"
                         "def test(context): pass\n")
    requirements = read_requirements(decorated)
    if requirements['min_peer'] != '2.6.0' or requirements['transports'] != ('pipe', 'tcp'):
        test_fail('smoke metadata was not discovered')
    if not applies_to_peer(requirements, '2.6.0', 27, 'pipe'):
        test_fail('matching peer metadata was rejected')
    if (applies_to_peer(requirements, '2.5.0', 27, 'pipe')
            or applies_to_peer(requirements, '2.6.0', 26, 'pipe')
            or applies_to_peer(requirements, '2.6.0', 27, 'socket')):
        test_fail('inapplicable peer metadata was accepted')

    module = context.scratch / 'module.py'
    module.write_text("from harness import metadata\nmetadata(features={'acl'}, tags={'version-mix'})\n")
    if read_requirements(module)['features'] != ('acl',):
        test_fail('module metadata was not discovered')

    alias = context.scratch / 'alias.py'
    alias.symlink_to(module.name)
    if resolve_test_path(alias) != module or read_requirements(alias) != read_requirements(module):
        test_fail('link alias did not inherit target metadata')

    placeholder = context.scratch / 'placeholder.py'
    placeholder.write_text(module.name)
    if (placeholder_target(placeholder) != module or resolve_test_path(placeholder) != module
            or read_requirements(placeholder) != read_requirements(module)):
        test_fail('placeholder did not inherit target metadata')

    cycle_a = context.scratch / 'cycle-a.py'
    cycle_b = context.scratch / 'cycle-b.py'
    cycle_a.write_text(cycle_b.name)
    cycle_b.write_text(cycle_a.name)
    try:
        read_requirements(cycle_a)
    except ValueError:
        pass
    else:
        test_fail('placeholder cycle was accepted')

    inferred = context.scratch / 'inferred.py'
    inferred.write_text("require_tcp('tcp')\nrequire_asan('asan')\n"
                        "setup_chroot_inner('chroot')\n"
                        "test_skipped('root', capability='nonroot')\n")
    if read_requirements(inferred)['features'] != ('asan', 'chroot', 'nonroot', 'root', 'tcp'):
        test_fail('capability metadata was not inferred')

    invalid_capability = context.scratch / 'invalid-capability.py'
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
        {'cost': 'slow'},
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
