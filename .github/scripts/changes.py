import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


FAMILIES = ('linux', 'bsd', 'macos', 'other', 'versions', 'android',
            'archive', 'diagnostics', 'valgrind', 'interoperability')
OUTPUTS = (*FAMILIES, 'asan', 'coverage', 'clang')
ARCHIVE_DOCUMENTS = {
    'rsync-ssl.1.md', 'rsyncd.conf.5.md', 'support/rrsync.1.md',
    'rsync.1', 'rsync.1.html', 'rsync-ssl.1', 'rsync-ssl.1.html',
    'rsyncd.conf.5', 'rsyncd.conf.5.html', 'rrsync.1', 'rrsync.1.html',
}


def empty():
    return {name: False for name in OUTPUTS}


def enable(selected, *names):
    for name in names:
        selected[name] = True


def full():
    return {name: True for name in OUTPUTS}


def documentation(path):
    name = PurePosixPath(path).name
    return (PurePosixPath(path).suffix in ('.md', '.markdown', '.rst', '.adoc')
            or name in ('README', 'NEWS', 'COPYING', 'COPYRIGHT', 'AUTHORS',
                        'CONTRIBUTING', 'SUPPORT', 'GOVERNANCE', 'INSTALL'))


def workflow(selected, name):
    mapping = {
        'ci.yml': OUTPUTS,
        'linux.yml': ('linux', 'diagnostics'),
        'bsd.yml': ('bsd', 'diagnostics'),
        'macos.yml': ('macos', 'diagnostics'),
        'other.yml': ('other', 'diagnostics'),
        'versions.yml': ('versions', 'diagnostics'),
        'android.yml': ('android', 'diagnostics'),
        'archive.yml': ('archive', 'diagnostics'),
        'diagnostics.yml': ('diagnostics', 'asan', 'coverage', 'clang'),
        'valgrind.yml': ('valgrind', 'diagnostics'),
        'interoperability.yml': ('interoperability', 'diagnostics'),
        'interoperability-cleanup.yml': ('interoperability', 'diagnostics'),
        'interoperability-tests.yml': ('interoperability', 'diagnostics'),
    }
    if name not in mapping:
        return False
    enable(selected, *mapping[name])
    return True


def script(selected, name):
    stem = name.rsplit('.', 1)[0]
    test = stem.startswith('test-')
    base = stem[5:] if test else stem
    if base.startswith('interop') or base in ('transfers',):
        enable(selected, 'interoperability', 'diagnostics')
    elif base in ('android', 'android-emulator', 'termux'):
        enable(selected, 'android', 'diagnostics')
    elif base == 'archive':
        enable(selected, 'archive', 'diagnostics')
    elif base == 'versions':
        enable(selected, 'versions', 'diagnostics')
    elif base == 'guest':
        enable(selected, 'linux', 'bsd', 'other', 'diagnostics')
    elif base == 'storage':
        enable(selected, 'linux', 'diagnostics')
    elif base == 'diagnostics':
        enable(selected, 'diagnostics')
        if not test:
            enable(selected, 'valgrind', 'asan', 'coverage', 'clang')
    elif base == 'changes' and not test:
        enable(selected, *OUTPUTS)
    elif base == 'check-results' and not test:
        enable(selected, *OUTPUTS)
    elif test and base in ('check-results', 'results', 'changes'):
        enable(selected, 'diagnostics')
    elif base == 'check-platform':
        enable(selected, 'linux', 'macos', 'other', 'diagnostics')
    elif base == 'installcheck':
        enable(selected, 'linux', 'diagnostics')
    elif name == 'install-cygwin.ps1':
        enable(selected, 'other', 'diagnostics')
    else:
        return False
    return True


def testsuite(selected, path):
    native = ('linux', 'bsd', 'macos', 'other', 'versions', 'diagnostics')
    if path in ('testsuite/fixtures/valgrind.supp', 'testsuite/profiles/valgrind.json'):
        enable(selected, 'valgrind', 'diagnostics')
        return
    if path == 'testsuite/profiles/archive.json':
        enable(selected, 'archive', 'diagnostics')
        return
    if path == 'testsuite/profiles/asan.json':
        enable(selected, 'diagnostics', 'asan')
        return
    if path == 'testsuite/harness/interop_metadata.py':
        enable(selected, 'interoperability', 'diagnostics')
        return
    if path in ('testsuite/tools/check-rsync-ssl.py', 'testsuite/tools/check-ssh.py',
                'testsuite/tools/check-storage.py'):
        enable(selected, 'linux', 'diagnostics')
        return
    enable(selected, *native)
    enable(selected, 'asan')
    if (path == 'testsuite/runtests.py' or path == 'testsuite/tools/check.py'
            or path.startswith('testsuite/harness/')):
        enable(selected, 'valgrind')
    if path == 'testsuite/harness/interoperability.py':
        enable(selected, 'interoperability')


def select(paths):
    if not paths:
        raise ValueError('No changed paths')
    selected = empty()
    for raw in paths:
        path = raw.replace('\\', '/')
        parsed = PurePosixPath(path)
        if not path or parsed.is_absolute() or '..' in parsed.parts:
            raise ValueError('Invalid changed path')
        if path == 'rsync.1.md':
            return full()
        if path in ARCHIVE_DOCUMENTS:
            enable(selected, 'archive', 'diagnostics')
        elif documentation(path):
            continue
        elif path.startswith('testsuite/'):
            testsuite(selected, path)
        elif path.startswith('old_versions/'):
            enable(selected, 'versions', 'diagnostics')
        elif path.startswith('.github/workflows/'):
            if not workflow(selected, parsed.name):
                return full()
        elif path.startswith('.github/scripts/'):
            if not script(selected, parsed.name):
                return full()
        elif path.startswith('.github/actions/interoperability/'):
            enable(selected, 'interoperability', 'diagnostics')
        elif path.startswith('.github/actions/'):
            return full()
        else:
            return full()
    return selected


def changed_paths(event):
    request = event.get('pull_request')
    if not isinstance(request, dict):
        raise ValueError('Pull request event has no request')
    base = request.get('base', {}).get('sha', '')
    head = request.get('head', {}).get('sha', '')
    if not re.fullmatch(r'[0-9a-f]{40}', base) or not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('Invalid pull request revisions')
    result = subprocess.run(['git', 'diff', '--name-only', '--no-renames', '-z', '--diff-filter=ACDMRTUXB',
                             base + '...' + head], capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError('Cannot determine changed paths')
    paths = [os.fsdecode(value) for value in result.stdout.split(b'\0') if value]
    if len(paths) > 10000:
        raise ValueError('Changed path count exceeds limit')
    return paths


def main():
    event_name = os.environ.get('GITHUB_EVENT_NAME')
    if event_name == 'pull_request':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        selected = select(changed_paths(event))
    else:
        selected = full()
    output = Path(os.environ['GITHUB_OUTPUT'])
    with output.open('a') as stream:
        for name in OUTPUTS:
            stream.write(f'{name}={str(selected[name]).lower()}\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(f'Change selection failed: {error}', file=sys.stderr)
        raise SystemExit(1)
