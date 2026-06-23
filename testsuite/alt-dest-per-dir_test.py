#!/usr/bin/env python3
"""Per-directory relative --compare/copy/link-dest paths.

An alt-dest arg prefixed with ": " is resolved from each destination file's
containing directory.  The fixture mirrors a restructure where an old flat file
already exists in the destination and the source now places it one level deeper.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_exists, assert_hardlinked, assert_not_exists, assert_not_hardlinked,
    assert_same, rmtree, run_rsync, test_fail,
)

old_rel = os.path.join('show', 'episode.mkv')
new_rel = os.path.join('show', 'season1', 'episode.mkv')


def seed_trees(old_name='episode.mkv'):
    rmtree(FROMDIR)
    rmtree(TODIR)
    (FROMDIR / 'show' / 'season1').mkdir(parents=True)
    (TODIR / 'show').mkdir(parents=True)
    data = b'episode payload\n'
    (FROMDIR / new_rel).write_bytes(data)
    (TODIR / 'show' / old_name).write_bytes(data)
    os.utime(FROMDIR / new_rel, (1000000000, 1000000000))
    os.utime(TODIR / 'show' / old_name, (1000000000, 1000000000))


def run_to(opt, *extra):
    seed_trees()
    run_rsync('-a', *extra, f'--{opt}=: ..', f'{FROMDIR}/', f'{TODIR}/')


run_to('link-dest')
assert_exists(TODIR / new_rel, label='link-dest per-dir result')
assert_hardlinked(TODIR / new_rel, TODIR / old_rel, label='link-dest : ..')

run_to('copy-dest')
assert_exists(TODIR / new_rel, label='copy-dest per-dir result')
assert_same(TODIR / new_rel, FROMDIR / new_rel, label='copy-dest : ..')
assert_not_hardlinked(TODIR / new_rel, TODIR / old_rel, label='copy-dest : ..')

run_to('compare-dest')
assert_not_exists(TODIR / new_rel, label='compare-dest : ..')

seed_trees(old_name='episode-old.mkv')
proc = run_rsync('-a', '--debug=FUZZY1', '--fuzzy', '--fuzzy',
                 '--link-dest=: ..', f'{FROMDIR}/', f'{TODIR}/',
                 capture_output=True)
out = proc.stdout + proc.stderr
if 'fuzzy basis selected for show/season1/episode.mkv: show/season1/../episode-old.mkv' not in out:
    test_fail(f"--fuzzy did not scan per-dir --link-dest=: .. basis:\n{out}")
assert_exists(TODIR / new_rel, label='fuzzy per-dir basis result')
assert_same(TODIR / new_rel, FROMDIR / new_rel, label='fuzzy : ..')
assert_not_hardlinked(TODIR / new_rel, TODIR / 'show' / 'episode-old.mkv',
                      label='fuzzy transfers delta')

print("alt-dest-per-dir: per-directory --compare/copy/link-dest basis paths verified")
