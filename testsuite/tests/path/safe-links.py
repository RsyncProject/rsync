#!/usr/bin/env python3

import os

from harness.rsync import SCRATCHDIR, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'safe-links'
rmtree(base)
base.mkdir(parents=True)

(base / 'sibling').write_text('SIBLING\n')

tree = base / 'tree'
tree.mkdir()
(tree / 'file').write_text('FILE\n')
(tree / 'sub').mkdir()
(tree / 'sub' / 'subfile').write_text('SUBFILE\n')

SHAPES = {
    'rel-same-dir':   ('link_a', 'file'),
    'rel-into-sub':   ('link_b', 'sub/subfile'),
    'rel-up-inside':  ('sub/link_c', '../file'),
    'rel-escape':     ('link_d', '../sibling'),
    'rel-deep-escape':('sub/link_e', '../../sibling'),
    'abs-target':     ('link_f', str(base / 'sibling')),
    'abs-inside':     ('link_g', str(tree / 'file')),
}
for _name, (where, val) in SHAPES.items():
    lp = tree / where
    lp.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(val, lp)

UNSAFE = {'rel-escape', 'rel-deep-escape', 'abs-target', 'abs-inside'}

def run(*args):
    run_rsync('-a', *args)

def fresh(dst):
    rmtree(dst)
    dst.mkdir(parents=True)

mismatches = []

dst = base / 'plain'
fresh(dst)
run(f'{tree}/', f'{dst}/')
for name, (where, value) in SHAPES.items():
    path = dst / where
    if not path.is_symlink() or os.readlink(path) != value:
        mismatches.append(f'default {name}: symlink was not preserved')

dst = base / 'safe_dst'
fresh(dst)
run('--safe-links', f'{tree}/', f'{dst}/')
for name, (where, _val) in SHAPES.items():
    present = (dst / where).is_symlink()
    want_present = name not in UNSAFE
    if present != want_present:
        mismatches.append(f'--safe-links {name}: present={present}, expected={want_present}')

dst = base / 'copy_dst'
fresh(dst)
run('--copy-unsafe-links', f'{tree}/', f'{dst}/')
for name, (where, _val) in SHAPES.items():
    p = dst / where
    is_link = p.is_symlink()
    is_file = p.is_file() and not is_link
    if name in UNSAFE:
        ok = is_file
    else:
        ok = is_link
    if not ok:
        mismatches.append(f'--copy-unsafe-links {name}: link={is_link}, file={is_file}')

dst = base / 'copy-all'
fresh(dst)
run('--copy-links', f'{tree}/', f'{dst}/')
for name, (where, _value) in SHAPES.items():
    path = dst / where
    if not path.is_file() or path.is_symlink():
        mismatches.append(f'--copy-links {name}: target was not copied')

dst = base / 'cut_inside'
fresh(dst)
run('--safe-links', f'{tree}/', f'{dst}/')
kept_inside = (dst / 'sub' / 'link_c').is_symlink()
if not kept_inside:
    mismatches.append('sub/link_c should be safe from the tree root')

dst = base / 'cut_sub'
fresh(dst)
run('--safe-links', f'{tree}/sub/', f'{dst}/')
kept_sub = (dst / 'link_c').is_symlink()
if kept_sub:
    mismatches.append('link_c should be unsafe from the subdirectory root')

for index, (source, cwd) in enumerate((('tree/', base), (str(tree.resolve()) + '/', None))):
    dst = base / f'path-{index}'
    fresh(dst)
    saved = os.getcwd()
    if cwd:
        os.chdir(cwd)
    try:
        run('--copy-unsafe-links', source, str(dst))
    finally:
        os.chdir(saved)
    if not (dst / 'link_d').is_file() or (dst / 'link_d').is_symlink():
        mismatches.append(f'path form {index}: unsafe link was not copied')

if mismatches:
    test_fail('safe-link behaviour changed:\n  ' + '\n  '.join(mismatches))

print('safe-link behaviour verified')
