#!/usr/bin/env python3
"""Explicit source directory symlinks remain transfer roots."""

import os
import pwd
import subprocess

from rsyncfns import SCRATCHDIR, rsync_argv, test_fail

real = SCRATCHDIR / 'real'
source = real / 'My_Documents'
source.mkdir(parents=True)
(source / 'marker').write_text('source contents\n')
absolute_link = SCRATCHDIR / 'home'
relative_link = SCRATCHDIR / 'relative-home'
os.symlink(str(real), absolute_link)
os.symlink(str(real), relative_link)

cases = (
    (('-r',), False),
    (('-rR',), True),
    (('-rR', '--no-inc-recursive'), True),
)

for link_name, link, cwd in (
    ('absolute', absolute_link, None),
    ('relative', relative_link, SCRATCHDIR),
):
    source_arg = (str(SCRATCHDIR) + '/./home/My_Documents/' if cwd is None
                  else 'relative-home/My_Documents/')
    for index, (options, relative) in enumerate(cases):
        dest = SCRATCHDIR / f'dest-{link_name}-descendant-{index}'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv(*options, source_arg, str(dest) + '/'),
            cwd=cwd, capture_output=True, text=True,
        )
        if proc.returncode:
            test_fail(f'{link_name} descendant source transfer with {options} '
                      f'failed: {proc.stdout}{proc.stderr}')
        expected = (dest / link.name / 'My_Documents' / 'marker' if relative
                    else dest / 'marker')
        if not expected.is_file() or expected.read_text() != 'source contents\n':
            test_fail('relative source layout or contents changed')

file_sources = (
    ('absolute-file', str(absolute_link / 'My_Documents' / 'marker'), None),
    ('relative-file', 'relative-home/My_Documents/marker', SCRATCHDIR),
)
for name, source_arg, cwd in file_sources:
    dest = SCRATCHDIR / f'dest-{name}'
    dest.mkdir()
    proc = subprocess.run(
        rsync_argv('-R', source_arg, str(dest) + '/'),
        cwd=cwd, capture_output=True, text=True,
    )
    if proc.returncode:
        test_fail(f'{name} transfer failed: {proc.stdout}{proc.stderr}')
    if cwd is None:
        expected = dest / str(absolute_link.relative_to('/')) / 'My_Documents' / 'marker'
    else:
        expected = dest / source_arg
    if not expected.is_file() or expected.read_text() != 'source contents\n':
        test_fail(f'{name} layout or contents changed')

files_from = SCRATCHDIR / 'files-from'
files_from.write_text('relative-home/My_Documents/marker\n')
dest = SCRATCHDIR / 'dest-files-from'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-r', f'--files-from={files_from}',
               str(SCRATCHDIR) + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
expected = dest / 'relative-home' / 'My_Documents' / 'marker'
if proc.returncode or not expected.is_file() or expected.read_text() != 'source contents\n':
    test_fail(f'files-from trusted ancestor transfer failed: '
              f'{proc.stdout}{proc.stderr}')

files_from.write_text('relative-home/My_Documents/\n')
dest = SCRATCHDIR / 'dest-files-from-dir'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-r', f'--files-from={files_from}',
               str(SCRATCHDIR) + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
expected = dest / 'relative-home' / 'My_Documents' / 'marker'
if proc.returncode or not expected.is_file() or expected.read_text() != 'source contents\n':
    test_fail(f'files-from trusted directory transfer failed: '
              f'{proc.stdout}{proc.stderr}')

remove_source = real / 'remove-marker'
remove_source.write_text('remove contents\n')
files_from.write_text('relative-home/remove-marker\n')
dest = SCRATCHDIR / 'dest-files-from-remove'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-r', '--remove-source-files', f'--files-from={files_from}',
               str(SCRATCHDIR) + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
expected = dest / 'relative-home' / 'remove-marker'
if proc.returncode or remove_source.exists() or not expected.is_file():
    test_fail(f'files-from remove-source-files failed: '
              f'{proc.stdout}{proc.stderr}')

confined_root = SCRATCHDIR / 'confined-root'
confined_root.mkdir()
confined_outside = SCRATCHDIR / 'confined-outside'
confined_outside.mkdir()
(confined_outside / 'marker').write_text('outside contents\n')
os.symlink(str(confined_outside), confined_root / 'outside-link')
confined_inside = confined_root / 'inside'
confined_inside.mkdir()
(confined_inside / 'marker').write_text('inside contents\n')
os.symlink(str(confined_inside), confined_root / 'inside-link')
confined_files_from = confined_root / 'files-from'

confined_files_from.write_text('inside-link/marker\n')
dest = confined_root / 'confined-inside-dest'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-r', f'--confine-root={confined_root}',
               f'--files-from={confined_files_from}',
               str(confined_root) + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
expected = dest / 'inside-link' / 'marker'
if proc.returncode or not expected.is_file() or expected.read_text() != 'inside contents\n':
    test_fail(f'files-from trusted in-root link failed under confinement: '
              f'{proc.stdout}{proc.stderr}')

confined_files_from.write_text('outside-link/marker\n')
dest = confined_root / 'confined-outside-dest'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-r', f'--confine-root={confined_root}',
               f'--files-from={confined_files_from}',
               str(confined_root) + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
escaped = dest / 'outside-link' / 'marker'
if proc.returncode == 0 or escaped.exists():
    test_fail(f'files-from followed a trusted link outside --confine-root: '
              f'{proc.stdout}{proc.stderr}')

confined_files_from.write_text('outside-link/\n')
dest = confined_root / 'confined-outside-dir-dest'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-r', f'--confine-root={confined_root}',
               f'--files-from={confined_files_from}',
               str(confined_root) + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
escaped = dest / 'outside-link' / 'marker'
if proc.returncode == 0 or escaped.exists():
    test_fail(f'files-from enumerated a trusted link outside --confine-root: '
              f'{proc.stdout}{proc.stderr}')

os.symlink(str(confined_root.parent), confined_root / 'ancestor-link')
confined_files_from.write_text('ancestor-link/\n')
dest = confined_root / 'confined-ancestor-dest'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-d', f'--confine-root={confined_root}',
               f'--files-from={confined_files_from}',
               str(confined_root) + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
if proc.returncode == 0 or (dest / 'ancestor-link').exists():
    test_fail(f'files-from accepted a trusted ancestor of --confine-root: '
              f'{proc.stdout}{proc.stderr}')

root_real = SCRATCHDIR / 'root-real'
root_real.mkdir()
(root_real / 'marker').write_text('source contents\n')
absolute_root_link = SCRATCHDIR / 'root-home'
relative_root_link = SCRATCHDIR / 'relative-root-home'
os.symlink(str(root_real), absolute_root_link)
os.symlink(str(root_real), relative_root_link)

for link_name, link, cwd in (
    ('absolute', absolute_root_link, None),
    ('relative', relative_root_link, SCRATCHDIR),
):
    source_arg = (str(SCRATCHDIR) + '/./root-home/' if cwd is None
                  else 'relative-root-home/')
    for index, (options, relative) in enumerate(cases):
        dest = SCRATCHDIR / f'dest-{link_name}-root-{index}'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv(*options, source_arg, str(dest) + '/'),
            cwd=cwd, capture_output=True, text=True,
        )
        if proc.returncode:
            test_fail(f'{link_name} root source transfer with {options} failed: '
                      f'{proc.stdout}{proc.stderr}')
        expected = dest / link.name / 'marker' if relative else dest / 'marker'
        if not expected.is_file() or expected.read_text() != 'source contents\n':
            test_fail('explicit source-root layout or contents changed')

if os.geteuid() == 0:
    untrusted_uid = next((entry.pw_uid for entry in pwd.getpwall()
                          if entry.pw_uid != 0), None)
    if untrusted_uid is not None:
        untrusted_link = SCRATCHDIR / 'untrusted-home'
        os.symlink(str(real), untrusted_link)
        os.lchown(untrusted_link, untrusted_uid, -1)
        source_arg = str(SCRATCHDIR) + '/./untrusted-home/'
        for index, (options, relative) in enumerate(cases[:2]):
            dest = SCRATCHDIR / f'untrusted-dest-{index}'
            dest.mkdir()
            proc = subprocess.run(
                rsync_argv(*options, source_arg, str(dest) + '/'),
                capture_output=True, text=True,
            )
            expected = dest / 'My_Documents' / 'marker'
            if relative:
                expected = dest / 'untrusted-home' / 'My_Documents' / 'marker'
            if proc.returncode or not expected.is_file():
                test_fail(f'explicit untrusted-owned source link with {options} '
                          f'failed: {proc.stdout}{proc.stderr}')

        dest = SCRATCHDIR / 'untrusted-file-dest'
        dest.mkdir()
        source_arg = str(untrusted_link / 'My_Documents' / 'marker')
        proc = subprocess.run(
            rsync_argv('-R', source_arg, str(dest) + '/'),
            capture_output=True, text=True,
        )
        expected = dest / str(untrusted_link.relative_to('/')) / 'My_Documents' / 'marker'
        if proc.returncode or not expected.is_file():
            test_fail(f'explicit untrusted-owned file path failed: '
                      f'{proc.stdout}{proc.stderr}')

        files_from.write_text('untrusted-home/My_Documents/marker\n')
        dest = SCRATCHDIR / 'untrusted-files-from-dest'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv('-r', f'--files-from={files_from}',
                       str(SCRATCHDIR) + '/', str(dest) + '/'),
            capture_output=True, text=True,
        )
        escaped = dest / 'untrusted-home' / 'My_Documents' / 'marker'
        if proc.returncode == 0 or escaped.exists():
            test_fail('files-from followed an untrusted-owned ancestor symlink')

        files_from.write_text('untrusted-home/My_Documents/\n')
        dest = SCRATCHDIR / 'untrusted-files-from-dir-dest'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv('-r', f'--files-from={files_from}',
                       str(SCRATCHDIR) + '/', str(dest) + '/'),
            capture_output=True, text=True,
        )
        escaped = dest / 'untrusted-home' / 'My_Documents' / 'marker'
        if proc.returncode == 0 or escaped.exists():
            test_fail('files-from enumerated an untrusted-owned ancestor symlink')

        files_from.write_text('untrusted-home/\n')
        dest = SCRATCHDIR / 'untrusted-files-from-leaf-dir-dest'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv('-r', f'--files-from={files_from}',
                       str(SCRATCHDIR) + '/', str(dest) + '/'),
            capture_output=True, text=True,
        )
        escaped = dest / 'untrusted-home' / 'marker'
        if proc.returncode == 0 or escaped.exists():
            test_fail('files-from followed an untrusted-owned directory link')

        files_from.write_text('untrusted-home/My_Documents/marker\n')
        dest = SCRATCHDIR / 'insecure-files-from-dest'
        dest.mkdir()
        proc = subprocess.run(
            rsync_argv('-r', '--insecure-links', f'--files-from={files_from}',
                       str(SCRATCHDIR) + '/', str(dest) + '/'),
            capture_output=True, text=True,
        )
        expected = dest / 'untrusted-home' / 'My_Documents' / 'marker'
        if proc.returncode or not expected.is_file():
            test_fail(f'files-from insecure-links opt-out failed: '
                      f'{proc.stdout}{proc.stderr}')

dest = SCRATCHDIR / 'remove-dest'
dest.mkdir()
proc = subprocess.run(
    rsync_argv('-r', '--remove-source-files', str(absolute_link / 'My_Documents') + '/', str(dest) + '/'),
    capture_output=True, text=True,
)
expected = dest / 'marker'
if proc.returncode or (source / 'marker').exists() or not expected.is_file():
    test_fail(f'remove-source-files failed: {proc.stdout}{proc.stderr}')
(source / 'marker').write_text('source contents\n')
