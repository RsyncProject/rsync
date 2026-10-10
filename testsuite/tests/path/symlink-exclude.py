#!/usr/bin/env python3

import os

from harness.daemon_config import write_daemon_conf
from harness.rsync import SCRATCHDIR, makepath, rmtree, run_rsync, start_test_daemon, test_fail

base = SCRATCHDIR / 'symlink-exclude'
rmtree(base)
base.mkdir()
modules = []

def module(name, exclude):
    root = base / name
    root.mkdir()
    modules.append((name, {'path': str(root), 'read only': 'no', 'exclude': exclude}))
    return root

destination = module('destination', '/secret/')
makepath(destination / 'secret')
(destination / 'secret' / 'f0').write_text('old destination\n')
(destination / 'blink').symlink_to('secret')

leaf = module('leaf', '/pub/blocked')
makepath(leaf / 'pub')
(leaf / 'blink').symlink_to('pub')

component = module('component', '/secret/')
makepath(component / 'secret')
(component / 'secret' / 'x').write_text('protected\n')
(component / 'blink').symlink_to('secret')

deep = module('deep', '/pub/secret/')
makepath(deep / 'pub' / 'secret')
(deep / 'pub' / 'secret' / 'x').write_text('protected\n')
(deep / 'alias').symlink_to('pub')

chdir = module('chdir', '/public/secret/')
makepath(chdir / 'public' / 'secret')
(chdir / 'public' / 'secret' / 'x').write_text('protected\n')
(chdir / 'alias').symlink_to('public')

metadata = module('metadata', '/pub/blocked')
makepath(metadata / 'pub')
meta_victim = metadata / 'pub' / 'blocked'
meta_victim.write_text('same\n')
os.utime(meta_victim, (1_000_000_000, 1_000_000_000))
(metadata / 'blink').symlink_to('pub')

backup = module('backup', '/secret/')
makepath(backup / 'secret')
(backup / 'secret' / 'f0').write_text('protected\n')
(backup / 'f0').write_text('old destination\n')
(backup / 'blink').symlink_to('secret')

partial = module('partial', '/secret/')
makepath(partial / 'secret')
(partial / 'secret' / 'f0').write_text('protected\n')
(partial / 'blink').symlink_to('secret')

leaf_backup = module('leaf-backup', '/public/f0')
makepath(leaf_backup / 'public')
(leaf_backup / 'public' / 'f0').write_text('protected\n')
(leaf_backup / 'f0').write_text('old destination\n')
(leaf_backup / 'blink').symlink_to('public')

leaf_partial = module('leaf-partial', '/public/f0')
makepath(leaf_partial / 'public')
(leaf_partial / 'public' / 'f0').write_text('protected\n')
(leaf_partial / 'blink').symlink_to('public')

url = start_test_daemon(write_daemon_conf(modules, name='symlink-exclude.conf'), 12962)

def source(name, relative, content='new\n'):
    root = base / f'{name}-source'
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text(content)
    return root

run_rsync('-a', f'{source("destination", "f0")}/', f'{url}destination/blink/', check=False)
if (destination / 'secret' / 'f0').read_text() != 'new\n':
    test_fail('destination symlink was not followed')

run_rsync('-a', '--keep-dirlinks', f'{source("leaf", "blink/blocked")}/', f'{url}leaf/', check=False)
if (leaf / 'pub' / 'blocked').read_text() != 'new\n':
    test_fail('excluded leaf was not reached through its logical alias')

run_rsync('-a', '--keep-dirlinks', f'{source("component", "blink/x")}/', f'{url}component/', check=False)
if (component / 'secret' / 'x').read_text() != 'new\n':
    test_fail('destination path component was not followed')

run_rsync('-a', '--keep-dirlinks', f'{source("deep", "alias/secret/x")}/', f'{url}deep/', check=False)
if (deep / 'pub' / 'secret' / 'x').read_text() != 'new\n':
    test_fail('deep destination symlink was not followed')

run_rsync('-a', '--keep-dirlinks', f'{source("chdir", "secret/x")}/', f'{url}chdir/alias/', check=False)
if (chdir / 'public' / 'secret' / 'x').read_text() != 'new\n':
    test_fail('symlinked destination directory was not followed')

meta_source = source('metadata', 'blink/blocked', 'same\n')
os.utime(meta_source / 'blink' / 'blocked', (1_000_999_999, 1_000_999_999))
run_rsync('-a', '--keep-dirlinks', f'{meta_source}/', f'{url}metadata/', check=False)
if int(meta_victim.stat().st_mtime) != 1_000_999_999:
    test_fail('metadata did not follow the logical alias')

run_rsync('-a', '--backup', '--backup-dir=/blink', f'{source("backup", "f0")}/',
          f'{url}backup/', check=False)
if (backup / 'secret' / 'f0').read_text() != 'old destination\n':
    test_fail('backup directory symlink was not followed')

run_rsync('-a', '--partial-dir=/blink', f'{source("partial", "f0")}/',
          f'{url}partial/', check=False)
victim = partial / 'secret' / 'f0'
if victim.exists() and victim.read_text() == 'protected\n':
    test_fail('partial directory symlink was not followed')

run_rsync('-a', '--backup', '--backup-dir=/blink', f'{source("leaf-backup", "f0")}/',
          f'{url}leaf-backup/', check=False)
if (leaf_backup / 'public' / 'f0').read_text() == 'protected\n':
    test_fail('backup directory symlink did not reach the excluded leaf')

run_rsync('-a', '--partial-dir=/blink', f'{source("leaf-partial", "f0")}/',
          f'{url}leaf-partial/', check=False)
victim = leaf_partial / 'public' / 'f0'
if victim.exists() and victim.read_text() == 'protected\n':
    test_fail('partial directory symlink did not reach the excluded leaf')

print('daemon excludes retain logical-name semantics across symlinked paths')
