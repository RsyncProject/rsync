import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packaging'))
from source_archive import GEN_FILES, make_archive


def run(*args, **kwargs):
    return subprocess.run(args, check=True, timeout=900, **kwargs)


def create(archive):
    version = re.search(r'#define RSYNC_VERSION "([A-Za-z0-9.+-]+)"', Path('version.h').read_text())
    if not version:
        raise ValueError('Cannot read release version')
    with tempfile.TemporaryDirectory(prefix='rsync-release-') as directory:
        stage = Path(directory) / ('rsync-' + version[1])
        stage.mkdir()
        for name in GEN_FILES:
            shutil.copy2(name, stage / name)
        make_archive('HEAD', stage, archive)


def qualify(archive, output):
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='rsync-archive-test-') as directory:
        root = Path(directory)
        root.chmod(0o755)
        with tarfile.open(archive) as source:
            members = source.getmembers()
            tops = {member.name.split('/')[0] for member in members}
            if len(tops) != 1 or not next(iter(tops)).startswith('rsync-'):
                raise ValueError('Invalid release archive root')
            if any(member.uid or member.gid for member in members):
                raise ValueError('Release archive contains non-root ownership')
            release_root = root / next(iter(tops))
            for member in members:
                parts = PurePosixPath(member.name).parts
                if PurePosixPath(member.name).is_absolute() or '..' in parts or '.git' in parts or '.github' in parts:
                    raise ValueError('Invalid release archive entry')
                path = (root / member.name).resolve()
                if path != release_root and release_root not in path.parents:
                    raise ValueError('Archive entry escapes extraction directory')
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as incoming, path.open('xb') as target:
                        shutil.copyfileobj(incoming, target)
                elif member.issym() or member.islnk():
                    target = ((path.parent if member.issym() else root) / member.linkname).resolve()
                    if target != release_root and release_root not in target.parents:
                        raise ValueError('Archive link escapes extraction directory')
                    if member.issym():
                        path.symlink_to(member.linkname)
                    else:
                        os.link(target, path)
                else:
                    raise ValueError('Unsupported release archive entry')
                if not member.issym():
                    path.chmod(member.mode)
                os.utime(path, (member.mtime, member.mtime), follow_symlinks=False)
        source = root / tops.pop()
        for name in GEN_FILES:
            if not (source / name).is_file() or not (source / name).stat().st_size:
                raise ValueError(f'Missing generated release file: {name}')
        for name in ('configure', 'configure.sh', 'rsync-ssl', 'support/rrsync'):
            if not os.access(source / name, os.X_OK):
                raise ValueError(f'Release file is not executable: {name}')
        blocked = root / 'blocked'
        blocked.mkdir()
        for name in ('git', 'autoconf', 'autoheader', 'automake', 'aclocal'):
            tool = blocked / name
            tool.write_text(f'#!{sys.executable}\nraise SystemExit("Unexpected build dependency: {name}")\n')
            tool.chmod(0o755)
        env = {**os.environ, 'PATH': str(blocked) + os.pathsep + os.environ['PATH'],
               'RSYNC_TEST_TMP': '/dev/shm'}
        build, prefix = root / 'build', root / 'installed'
        build.mkdir()
        with (output / 'build.log').open('w') as log:
            run(str(source / 'configure'), '--with-rrsync', '--disable-md2man', '--prefix=' + str(prefix),
                cwd=build, env=env, stdout=log, stderr=subprocess.STDOUT)
            run('make', '-j2', 'check-progs', cwd=build, env=env, stdout=log, stderr=subprocess.STDOUT)
        privilege = 'root' if os.geteuid() == 0 else 'nonroot'
        for transport in ('pipe', 'tcp'):
            profiles = f'non-asan,{privilege},self-peer,linux,no-userns,archive'
            args = [] if transport == 'pipe' else ['--use-tcp', '--daemon-tests-only']
            if transport == 'pipe':
                profiles = 'pipe,' + profiles
            results = root / 'results' / transport
            try:
                with (output / f'{transport}.log').open('w') as log:
                    run(sys.executable, '-B', '-S', str(source / 'testsuite/tools/check.py'), '.', './rsync',
                        profiles, str(results), *args, '-j4', cwd=build, env=env,
                        stdout=log, stderr=subprocess.STDOUT)
            finally:
                retained = output / transport
                retained.mkdir()
                for name in ('receipt.json', 'run.log'):
                    if (results / name).is_file():
                        shutil.copy2(results / name, retained / name)
        run('make', 'install', cwd=build, env=env)
        required = ('bin/rsync', 'bin/rsync-ssl', 'bin/rrsync', 'share/man/man1/rsync.1',
                    'share/man/man1/rsync-ssl.1', 'share/man/man1/rrsync.1', 'share/man/man5/rsyncd.conf.5')
        for name in required:
            if not (prefix / name).is_file() or not (prefix / name).stat().st_size:
                raise ValueError(f'Missing installed release file: {name}')
        data = root / 'data'
        data.mkdir()
        (data / 'file').write_bytes(b'installed rsync\n')
        destination = root / 'copy'
        run(str(prefix / 'bin/rsync'), '-a', str(data) + '/', str(destination))
        if (destination / 'file').read_bytes() != (data / 'file').read_bytes():
            raise ValueError('Installed rsync changed file contents')
        rrsync = prefix / 'bin/rrsync'
        configured, count = re.subn(r'(?m)^RSYNC = .+$',
                                    lambda match: f'RSYNC = {str(prefix / "bin/rsync")!r}',
                                    rrsync.read_text(), count=1)
        if count != 1:
            raise ValueError('Cannot configure installed rrsync executable')
        rrsync.write_text(configured)
        remote = root / 'remote'
        remote.write_text(f'#!{sys.executable}\nimport os, shlex, sys\n'
                          'os.environ["SSH_ORIGINAL_COMMAND"] = " ".join(map(shlex.quote, sys.argv[2:]))\n'
                          f'os.execv({str(prefix / "bin/rrsync")!r}, ["rrsync", {str(data)!r}])\n')
        remote.chmod(0o755)
        installed_env = {**env, 'PATH': str(prefix / 'bin') + os.pathsep + env['PATH']}
        restricted = root / 'restricted'
        run(str(prefix / 'bin/rsync'), '-a', '-e', str(remote), 'dummy:file', str(restricted), env=installed_env)
        if restricted.read_bytes() != (data / 'file').read_bytes():
            raise ValueError('Installed rrsync changed file contents')
        run(sys.executable, '-B', '-S', str(source / 'testsuite/tools/check-rsync-ssl.py'),
            '--rsync', str(prefix / 'bin/rsync'), '--rsync-ssl', str(prefix / 'bin/rsync-ssl'),
            '--openssl', shutil.which('openssl'), env=env)
        sentinel = prefix / 'unrelated'
        sentinel.write_text('retained\n')
        run('make', 'uninstall', cwd=build, env=env)
        remaining = {path.relative_to(prefix).as_posix() for path in prefix.rglob('*')
                     if path.is_file() or path.is_symlink()}
        if remaining != {'unrelated'} or sentinel.read_text() != 'retained\n':
            raise ValueError(f'Incorrect uninstall result: {remaining}')
    (output / 'qualification.json').write_text(json.dumps({'archive_sha256': digest, 'result': 'pass'}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=('create', 'test'))
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    if args.task == 'create':
        archive.parent.mkdir(parents=True, exist_ok=True)
        create(archive)
    else:
        if args.output is None:
            parser.error('--output is required for testing')
        output = args.output.resolve()
        output.mkdir(parents=True)
        qualify(archive, output)


if __name__ == '__main__':
    main()
