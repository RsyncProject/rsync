#!/usr/bin/env python3

import json
import os
import stat
import subprocess

from harness import TestContext, requires, run
from harness.rsync import rsync_argv, run_rsync


@requires(transports={'pipe'}, min_peer={'primary': '4.0.0', 'secondary': '2.6.0'},
          mutates={'filesystem', 'process'}, tags={'metadata', 'regression'})
def test(context: TestContext):
    context.source.mkdir()
    context.destination.mkdir()
    source = context.source / 'record'
    source.write_text('new data\n', encoding='ascii')
    target = context.destination / 'record'
    target.write_text('old data\n', encoding='ascii')
    batch = context.scratch / 'batch'
    version = json.loads(run_rsync('-VV', capture_output=True).stdout)
    native = version['capabilities'].get('fileflags', False)

    def refuse(options, *paths, diagnostic):
        result = subprocess.run(rsync_argv(*options, *map(str, paths)), input='',
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 1 and diagnostic in result.stderr, result.stderr
        assert target.read_text(encoding='ascii') == 'old data\n'
        assert not list(batch.parent.glob('batch*'))

    if not native:
        refuse(['--fileflags'], source, target, diagnostic='--fileflags is not supported')

    run_rsync('--fileflags', '--no-fileflags', '--ignore-times', str(source), str(target))
    assert target.read_text(encoding='ascii') == 'new data\n'
    target.write_text('old data\n', encoding='ascii')

    if not native:
        return

    for flag in ('--write-batch', '--only-write-batch'):
        refuse(['--fileflags', f'{flag}={batch}'], source, target, diagnostic='batch')
    refuse(['--fileflags', f'--read-batch={batch}'], target, diagnostic='batch')
    refuse(['--server', '--fileflags'], '.', target, diagnostic='local transfers')
    refuse(['--fileflags', '-e', '/no-such-remote-shell'], source, 'host:target',
           diagnostic='local transfers')
    refuse(['--fileflags'], 'host::module/record', target, diagnostic='local transfers')
    for flag in ('--inplace', '--partial', '--delay-updates', '--backup', '--hard-links',
                 '--insecure-links', '-M--no-fileflags', f'--temp-dir={context.scratch}',
                 f'--link-dest={context.source}'):
        refuse(['--fileflags', flag], source, target, diagnostic='--fileflags cannot be combined')

    nodump = stat.UF_NODUMP
    hidden = stat.UF_HIDDEN
    immutable = stat.UF_IMMUTABLE
    subdir = context.source / 'subdir'
    subdir.mkdir()
    nested = subdir / 'nested'
    nested.write_text('nested data\n', encoding='ascii')
    nested_dest = context.destination / 'subdir' / 'nested'
    symlink = context.source / 'link'
    symlink.symlink_to('record')
    os.chflags(context.source, nodump)
    os.chflags(subdir, nodump)
    os.chflags(nested, nodump)
    os.chflags(context.destination, hidden)
    os.chflags(source, nodump | immutable)

    metadata_options = [flag for capability, flag in (('ACLs', '-A'), ('xattrs', '-X'))
                        if version['capabilities'].get(capability)]

    def transfer(*options, check=True):
        return run_rsync('-a', '--fileflags', *options, f'{context.source}/',
                         f'{context.destination}/', check=check, capture_output=True)

    try:
        baseline = target.stat()
        transfer('--dry-run', '--ignore-times')
        assert target.stat().st_ino == baseline.st_ino
        assert target.stat().st_flags == baseline.st_flags
        assert target.read_text(encoding='ascii') == 'old data\n'
        assert context.destination.stat().st_flags == hidden
        assert not nested_dest.exists()

        transfer('--ignore-times', *metadata_options)
        assert target.read_text(encoding='ascii') == 'new data\n'
        assert target.stat().st_flags == nodump
        assert context.destination.stat().st_flags == nodump | hidden
        assert nested_dest.stat().st_flags == nodump
        assert nested_dest.parent.stat().st_flags == nodump
        assert (context.destination / 'link').is_symlink()
    finally:
        os.chflags(source, 0)

    # Equal data and mtime must take the metadata-only path, even without read access.
    os.chflags(target, nodump | hidden)
    source.chmod(0)
    target.chmod(0)
    inode = target.stat().st_ino
    try:
        for flag in ('--dry-run', '--no-inc-recursive'):
            result = transfer('--itemize-changes', flag)
            assert 'record' in result.stdout, result.stdout
            assert target.stat().st_ino == inode and stat.S_IMODE(target.stat().st_mode) == 0
            assert target.stat().st_flags == (nodump | hidden if flag == '--dry-run' else hidden)
    finally:
        source.chmod(0o644)
        target.chmod(0o644)

    # Refusal leaves the existing inode, flags, data and directory entries intact.
    os.chflags(source, nodump)
    os.chflags(target, immutable)
    entries = sorted(path.name for path in context.destination.iterdir())
    try:
        for options in ((), ('--ignore-times', '--force')):
            result = transfer(*options, check=False)
            assert result.returncode == 23, (result.returncode, result.stderr)
            assert target.stat().st_ino == inode and target.stat().st_flags == immutable
            assert target.read_text(encoding='ascii') == 'new data\n'
            assert sorted(path.name for path in context.destination.iterdir()) == entries
    finally:
        os.chflags(target, 0)

    # A non-empty directory protected against removal must not be emptied first.
    protected = context.destination / 'protected-dir'
    protected.mkdir()
    child = protected / 'child'
    child.write_text('keep me\n', encoding='ascii')
    nounlink = 0x10  # FreeBSD UF_NOUNLINK
    os.chflags(protected, nounlink)
    try:
        result = transfer('--delete', check=False)
        assert result.returncode == 23, (result.returncode, result.stderr)
        assert protected.stat().st_flags & nounlink
        assert child.read_text(encoding='ascii') == 'keep me\n'
    finally:
        os.chflags(protected, 0)

    (context.source / protected.name).write_text('replacement\n', encoding='ascii')
    result = transfer('--force', check=False)
    assert result.returncode == 23, (result.returncode, result.stderr)
    assert child.read_text(encoding='ascii') == 'keep me\n'

    # A failed update on the temporary inode must not install it.
    fixture = context.repository / 'testsuite/fixtures/fileflags-set-error.c'
    library = context.scratch / 'fileflags-set-error.so'
    subprocess.run(['cc', '-shared', '-fPIC', '-o', str(library), str(fixture)],
                   check=True, timeout=30)
    target.write_text('keep old data\n', encoding='ascii')
    baseline = target.stat()
    entries = sorted(path.name for path in context.destination.iterdir())
    result = subprocess.run(rsync_argv('--fileflags', '--ignore-times', str(source), str(target)),
                            env={**os.environ, 'LD_PRELOAD': str(library)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 23 and 'failed to set file flags' in result.stderr, result.stderr
    assert target.stat().st_ino == baseline.st_ino and target.stat().st_flags == baseline.st_flags
    assert target.read_text(encoding='ascii') == 'keep old data\n'
    assert sorted(path.name for path in context.destination.iterdir()) == entries


if __name__ == '__main__':
    run(test)
