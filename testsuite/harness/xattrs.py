import os
import platform
import re
import shutil
import subprocess

from .process import run_rsync

SYSTEM = platform.system()
CYGWIN = SYSTEM.startswith('CYGWIN')
LINUX_NAMESPACE = SYSTEM == 'Linux' or CYGWIN

RSYNC_PREFIX = 'rsync'
RUSR = 'rsync.nonuser' if SYSTEM in ('Darwin', 'SunOS') else 'rsync'

class XattrError(OSError):
    pass

def _full_name(name):
    return f'user.{name}' if LINUX_NAMESPACE else name

def is_supported():
    command = os.environ['RSYNC']
    version = run_rsync(command, '-VV', capture_output=True).stdout
    if '"xattrs": true' not in version:
        return False
    if SYSTEM == 'Linux':
        return hasattr(os, 'setxattr')
    if CYGWIN:
        return shutil.which('setfattr') is not None
    if SYSTEM == 'Darwin':
        return shutil.which('xattr') is not None
    if SYSTEM == 'FreeBSD':
        return shutil.which('setextattr') is not None
    if SYSTEM == 'SunOS':
        return shutil.which('runat') is not None
    return False

def _tool_errno(tool, message):
    if tool != 'xattr' or not message:
        return None
    match = re.match(r'xattr: \[Errno (\d+)\]', message.splitlines()[0])
    return int(match.group(1)) if match else None

def _run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, **kwargs)
    if result.returncode == 0:
        return result
    message = (result.stderr or result.stdout or '').strip()
    if isinstance(message, bytes):
        message = message.decode('utf-8', 'replace')
    error = XattrError(f'{argv[0]} exited {result.returncode}'
                       + (f': {message}' if message else ''))
    error.errno = _tool_errno(argv[0], message)
    raise error

def get_value(name, path):
    name = _full_name(name)
    path = str(path)
    if SYSTEM == 'Linux':
        return os.getxattr(path, name)
    if CYGWIN:
        argv = ['getfattr', '--only-values', '-n', name, path]
        options = {}
    elif SYSTEM == 'Darwin':
        argv = ['xattr', '-px', name, path]
        options = {}
    elif SYSTEM == 'FreeBSD':
        argv = ['getextattr', '-qqh', 'user', name, path]
        options = {}
    elif SYSTEM == 'SunOS':
        argv = ['runat', path, '/bin/sh']
        options = {'input': b'cat "$XNAME"\n', 'env': {**os.environ, 'XNAME': name}}
    else:
        raise NotImplementedError(f'xattr get on {SYSTEM}')
    result = _run(argv, **options)
    if SYSTEM != 'Darwin':
        return result.stdout
    try:
        return bytes.fromhex(result.stdout.decode('ascii'))
    except (UnicodeDecodeError, ValueError) as cause:
        error = XattrError('xattr returned invalid hexadecimal')
        error.errno = None
        raise error from cause

def set_value(name, value, *paths):
    name = _full_name(name)
    for path in map(str, paths):
        if SYSTEM == 'Linux':
            os.setxattr(path, name.encode(), value.encode())
        elif CYGWIN:
            _run(['setfattr', '-n', name, '-v', value, path], text=True)
        elif SYSTEM == 'Darwin':
            _run(['xattr', '-w', name, value, path], text=True)
        elif SYSTEM == 'FreeBSD':
            _run(['setextattr', '-h', 'user', name, value, path], text=True)
        elif SYSTEM == 'SunOS':
            _run(['runat', path, '/bin/sh'], input='printf %s "$XVAL" > "$XNAME"\n',
                 env={**os.environ, 'XNAME': name, 'XVAL': value}, text=True)
        else:
            raise NotImplementedError(f'xattr set on {SYSTEM}')

def dump_values(*paths):
    if SYSTEM == 'Linux':
        output = []
        for path in map(str, paths):
            names = sorted(name for name in os.listxattr(path) if name.startswith('user.'))
            if not names:
                continue
            output.append(f'# file: {path}\n')
            for name in names:
                value = os.getxattr(path, name).decode('utf-8', 'surrogateescape')
                output.append(f'{name}="{value}"\n')
            output.append('\n')
        return ''.join(output)
    if CYGWIN:
        return subprocess.check_output(['getfattr', '-d', *map(str, paths)], text=True)
    if SYSTEM == 'Darwin':
        output = []
        for path in paths:
            value = subprocess.check_output(['xattr', '-l', str(path)], text=True)
            output.append('\n'.join(line.lstrip(' \t') for line in value.splitlines()))
            output.append('\n')
        return ''.join(output)
    if SYSTEM == 'FreeBSD':
        output = []
        for path in paths:
            names = subprocess.check_output(
                ['lsextattr', '-q', '-h', 'user', str(path)], text=True).split()
            for name in sorted(names):
                output.append(subprocess.check_output(
                    ['getextattr', '-h', 'user', name, str(path)], text=True))
        return ''.join(output)
    if SYSTEM == 'SunOS':
        script = ('for x in *; do case "$x" in SUNWattr_*) continue;; esac; '
                  'printf "%s=%s\\n" "$x" "$(cat "$x")"; done\n')
        return ''.join(subprocess.run(
            ['runat', str(path), '/bin/sh'], input=script,
            capture_output=True, text=True, check=True,
        ).stdout for path in paths)
    raise NotImplementedError(f'xattr dump on {SYSTEM}')
