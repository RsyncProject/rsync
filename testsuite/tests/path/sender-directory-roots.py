#!/usr/bin/env python3

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_same, makepath
from harness.rsync import run_rsync

metadata(features={'symlink'}, transports={'pipe'}, mutates={'filesystem'},
         tags={'path', 'regression'})

source = SCRATCHDIR / 'source'
sources = [source / name for name in ('first', 'second')]
entries = ('common/deep/one', 'common/deep/two', 'common/deeper/one')
for root in sources:
    for entry in entries:
        path = root / entry
        makepath(path.parent)
        path.write_text(f'{root.name}: {entry}\n')

for name, options in (('incremental', ()), ('non-incremental', ('--no-inc-recursive',))):
    dest = SCRATCHDIR / name
    makepath(dest)
    run_rsync('-a', *options, *(str(root) for root in sources), f'{dest}/')
    for root in sources:
        for entry in entries:
            assert_same(root / entry, dest / root.name / entry, label=name)

alias = source / 'alias'
alias.symlink_to('first', target_is_directory=True)
dest = SCRATCHDIR / 'alias-scan'
run_rsync('-a', f'{alias}/', f'{dest}/')
for entry in entries:
    assert_same(sources[0] / entry, dest / entry, label='explicit source alias')
