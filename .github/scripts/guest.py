import argparse
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import signal
import struct
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('platform', choices=['freebsd', 'netbsd', 'openbsd', 'solaris', 'alpine', 'check'])
    parser.add_argument('--release')
    parser.add_argument('--output', type=Path, default=Path('test-results'))
    args = parser.parse_args()
    if args.platform == 'check':
        try:
            with (args.output / 'status').open('rb') as stream:
                status = stream.read(3)
        except OSError as error:
            print(f'Guest results unavailable: {error.strerror}', file=sys.stderr)
            return 1
        if status not in (b'0', b'0\n'):
            print('Guest checks failed or produced an invalid status', file=sys.stderr)
            return 1
        return 0

    os.umask(0o022)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    status = 1
    active = None
    cross_device = None
    mounted = False
    source = None
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}

    def cancel(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if active is not None and active.poll() is None:
            active.terminate()
            try:
                active.wait(timeout=10)
            except subprocess.TimeoutExpired:
                active.kill()
                active.wait(timeout=5)
        raise SystemExit(128 + signum)

    def command(label, argv, *, required=True, destination=None, account=None):
        nonlocal active
        print(f'::group::{label}', flush=True)
        options = {}
        current_env = env.copy()
        if account:
            options = dict(user=account.pw_uid, group=account.pw_gid,
                           extra_groups=os.getgrouplist(account.pw_name, account.pw_gid))
            current_env['HOME'] = account.pw_dir
        stream = None
        try:
            stream = destination.open('w') if destination else None
            active = subprocess.Popen(argv, env=current_env, stdout=stream, **options)
            result = active.wait()
            if result and required:
                raise subprocess.CalledProcessError(result, argv)
            return result
        except OSError as error:
            if required:
                raise
            print(f'::error::{label}: {error}', file=sys.stderr)
            return 2
        finally:
            active = None
            if stream:
                stream.close()
            print('::endgroup::', flush=True)

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        if args.platform == 'alpine':
            shutil.copytree('/source', '/work', symlinks=True)
            os.chdir('/work')
        source = Path.cwd()
        expected = {'freebsd': 'FreeBSD', 'netbsd': 'NetBSD', 'openbsd': 'OpenBSD',
                    'solaris': 'SunOS', 'alpine': 'Linux'}[args.platform]
        command('Platform', ['uname', '-a'])
        if platform.system() != expected:
            raise ValueError(f'Expected {expected}, got {platform.system()}')
        if args.platform in ('freebsd', 'netbsd', 'openbsd'):
            actual = platform.release()
            if platform.machine() != 'amd64' or not args.release or not (
                    actual == args.release or args.platform == 'freebsd' and actual.startswith(args.release + '-RELEASE')):
                raise ValueError(f'Unexpected guest identity: {actual} {platform.machine()}')
        if args.platform == 'alpine':
            actual = subprocess.check_output(['apk', '--print-arch'], text=True).strip()
            if actual != env['APK_ARCH'] or struct.calcsize('P') * 8 != int(env['BITS']):
                raise ValueError('Unexpected Alpine userspace architecture')

        flags = ['--with-rrsync']
        features = []
        profiles = f'non-asan,self-peer,{args.platform}'
        if args.platform == 'freebsd':
            env.update(CPPFLAGS='-I/usr/local/include', LDFLAGS='-L/usr/local/lib')
            flags += ['--disable-md2man', '--enable-iconv', '--enable-acl-support', '--enable-xattr-support',
                      '--enable-roll-simd', '--enable-roll-asm', '--enable-md5-asm']
            features = ['zstd', 'lz4', 'xxh128', 'xxh3', 'xxh64', 'iconv', 'IDN', 'ACLs', 'xattrs']
            cross_device = Path(tempfile.mkdtemp(prefix='rsync-cross-device.'))
            command('Cross-device filesystem', ['mount', '-t', 'tmpfs', '-o', 'size=256m,mode=1777,nosuid',
                                                 'tmpfs', str(cross_device)])
            mounted = True
            env['RSYNC_TEST_TMP'] = str(cross_device)
        elif args.platform in ('netbsd', 'solaris'):
            flags += ['--disable-zstd', '--disable-md2man', '--disable-xxhash', '--disable-lz4', '--disable-idn']
            profiles += ',no-idn,no-xxhash,no-zstd-threads'
        elif args.platform == 'openbsd':
            env.update(AUTOCONF_VERSION='2.71', AUTOMAKE_VERSION='1.16',
                       CPPFLAGS='-I/usr/local/include -DXXH_INLINE_ALL=1', LDFLAGS='-L/usr/local/lib',
                       ac_cv_search_XXH64_createState='',
                       RSYNC_EXCLUDE='acl-symlink-race,sender-symlink-target-race,sender-remove-source-secure')
            flags += ['--disable-md2man', '--enable-iconv']
            features = ['zstd', 'lz4', 'xxh128', 'xxh3', 'xxh64', 'iconv', 'IDN']
        else:
            flags += ['--enable-iconv', '--enable-acl-support', '--enable-xattr-support']
            features = ['zstd', 'lz4', 'xxh128', 'xxh3', 'xxh64', 'iconv', 'IDN', 'ACLs', 'xattrs']
            profiles = env['PROFILES']
        command('Configure', ['./configure', *flags])
        command('Build', ['make', *(['-j4'] if args.platform == 'alpine' else []), 'check-progs'])
        status = int(bool(command('Version', ['./rsync', '--version'], required=False)))
        if args.platform == 'alpine':
            binary = (source / 'rsync').read_bytes()
            version = json.loads(subprocess.check_output(['./rsync', '-VV'], text=True))
            if binary[:4] != b'\x7fELF' or binary[4] != int(env['BITS']) // 32 or version['capabilities']['file_bits'] != 64:
                raise ValueError('Unexpected Alpine binary or file-offset width')
        if features:
            status |= bool(command('Features', [sys.executable, '-S', 'testsuite/tools/check-features.py',
                                               str(source / 'rsync'), *features], required=False,
                                   destination=output / 'features.json'))
        if args.platform == 'alpine':
            for argv in (['addgroup', '-S', 'rsynctest'], ['addgroup', '-S', 'rsynctest-extra'],
                         ['adduser', '-S', '-D', '-h', '/home/rsynctest', '-G', 'rsynctest', 'rsynctest'],
                         ['addgroup', 'rsynctest', 'rsynctest-extra']):
                command('Test account', argv)
        account = None if args.platform == 'solaris' else pwd.getpwnam('rsynctest')
        if account:
            (output / 'nonroot').mkdir()
            os.chown(output / 'nonroot', account.pw_uid, account.pw_gid)
        if args.platform != 'alpine':
            status |= bool(command('Build directory probe', ['make', 'dirfd-probe'], required=False))
            if account:
                status |= bool(command('Directory descriptors', [str(source / 'dirfd-probe'), str(output / 'nonroot')],
                                       required=False, account=account,
                                       destination=output / 'nonroot' / 'dirfd-probe.tsv'))
        passes = [('root', 'pipe', None), ('root', 'tcp', None)]
        if args.platform == 'alpine':
            passes[1:1] = [('root', 'protocol-30', 30), ('root', 'protocol-29', 29)]
        if account:
            passes += [('nonroot', 'pipe', None), ('nonroot', 'tcp', None)]
        for role, transport, protocol in passes:
            selected = f'{role},{profiles}' + ('' if transport == 'tcp' else ',pipe')
            options = []
            if transport == 'tcp':
                options += ['--use-tcp', '--daemon-tests-only']
            if protocol:
                selected += f',protocol-{protocol}'
                options += [f'--protocol={protocol}']
            parallel = 4 if args.platform == 'alpine' else 2 if args.platform == 'openbsd' and transport == 'tcp' else 8
            status |= bool(command(f'Tests ({"non-root" if role == "nonroot" else role} {transport})',
                                   [sys.executable, '-S', 'testsuite/tools/check.py', str(source), str(source / 'rsync'),
                                    selected, str(output / role / transport), *options, f'-j{parallel}'],
                                   required=False, account=account if role == 'nonroot' else None))
    except (KeyError, OSError, ValueError, subprocess.SubprocessError, SystemExit) as error:
        print(f'::error::Guest checks failed: {error}', file=sys.stderr)
        status = 1
    finally:
        if cross_device is not None:
            try:
                if mounted:
                    subprocess.run(['umount', str(cross_device)], check=True)
                cross_device.rmdir()
            except (OSError, subprocess.SubprocessError) as error:
                print(f'::error::Cross-device cleanup failed: {error}', file=sys.stderr)
                status = 1
        if args.platform == 'alpine' and source is not None:
            (output / 'bin').mkdir(exist_ok=True)
            for name in ('rsync', 'rsync-ssl', 'rsync.1', 'rsync-ssl.1', 'rsyncd.conf.5', 'rrsync.1', 'rrsync'):
                if (source / name).is_file():
                    shutil.copy2(source / name, output / 'bin' / name)
            if (source / 'config.log').is_file():
                shutil.copyfile(source / 'config.log', output / 'config.log')
        (output / 'status').write_text(f'{int(status)}\n')
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return int(status) if args.platform == 'alpine' else 0


if __name__ == '__main__':
    raise SystemExit(main())
