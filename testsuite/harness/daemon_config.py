import os
from pathlib import Path

from .daemon import start_test_daemon
from .filesystem import makepath, rmtree
from .process import under_valgrind
from .results import test_skipped

SCRATCHDIR = Path(os.environ['scratchdir'])
FROMDIR = SCRATCHDIR / 'from'
TODIR = SCRATCHDIR / 'to'

def _write_ignore23():
    path = SCRATCHDIR / 'ignore23'
    if path.exists():
        return
    path.write_text(
        '#!/bin/sh\n'
        'if "${@}"; then exit; fi\n'
        'ret=$?\n'
        'if test $ret = 23; then exit; fi\n'
        'exit $ret\n'
    )
    path.chmod(0o755)

def write_daemon_conf(modules, global_options=None, *,
                      name: str = 'test-rsyncd.conf') -> Path:
    options = {
        'pid file': str(SCRATCHDIR / 'rsyncd.pid'),
        'use chroot': 'no',
        'hosts allow': 'localhost 127.0.0.0/8',
        'log file': str(SCRATCHDIR / 'rsyncd.log'),
        'max verbosity': '4',
    }
    if global_options:
        options.update(global_options)
    if os.getuid() == 0:
        options.setdefault('uid', '0')
        options.setdefault('gid', '0')
    else:
        options.pop('uid', None)
        options.pop('gid', None)

    lines = [f'{key} = {value}' for key, value in options.items()]
    lines.append('')
    for module, parameters in modules:
        lines.append(f'[{module}]')
        lines.extend(f'\t{key} = {value}' for key, value in parameters.items())
        lines.append('')
    path = SCRATCHDIR / name
    path.write_text('\n'.join(lines) + '\n')
    _write_ignore23()
    return path

def build_rsyncd_conf() -> Path:
    log_format = '%i %h [%a] %m (%u) %l %f%L'
    modules = [
        ('test-from', {
            'path': str(FROMDIR), 'log format': log_format,
            'read only': 'yes', 'comment': 'r/o',
        }),
        ('test-to', {
            'path': str(TODIR), 'log format': log_format,
            'read only': 'no', 'comment': 'r/w',
        }),
        ('test-scratch', {
            'path': str(SCRATCHDIR), 'log format': log_format,
            'read only': 'no',
        }),
        ('test-hidden', {'path': str(FROMDIR), 'list': 'no'}),
    ]
    return write_daemon_conf(
        modules,
        global_options={
            'munge symlinks': 'no',
            'transfer logging': 'yes',
            'exclude': '? foobar.baz',
        },
    )

def setup_chroot_inner(name):
    if os.getuid() != 0:
        test_skipped('chroot /./ module regression requires root', capability='root')
    rsync = os.environ['RSYNC']
    peer = os.environ.get('RSYNC_PEER', rsync)
    if under_valgrind(rsync, peer):
        test_skipped('daemon chroot prevents valgrind from writing its per-process log',
                     capability='chroot')
    base = SCRATCHDIR / name
    outer = base / 'outer'
    inner = outer / 'inner'
    outside = outer / 'outside'
    source = base / 'src'
    rmtree(base)
    makepath(inner, outside, source)
    os.symlink('../outside', inner / 'linkparent')
    config = write_daemon_conf(
        [('mod', {
            'path': str(outer) + '/./inner', 'read only': 'no',
            'use chroot': 'yes', 'munge symlinks': 'no',
        })],
        global_options={
            'pid file': str(base / 'rsyncd.pid'),
            'log file': str(base / 'rsyncd.log'),
        },
        name=f'{name}.conf',
    )
    url = start_test_daemon(config, 12940 + (abs(hash(name)) % 200))
    return base, inner, outside, source, url
