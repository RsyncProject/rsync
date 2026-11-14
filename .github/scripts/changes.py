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
NO_TEST_FILES = {
    'COPYING', 'INSTALL.md', 'NEWS.md', 'README.md', 'SECURITY.md', 'TODO',
    'csprotocol.txt', 'tech_report.tex', '.github/INTEROPERABILITY.md',
    'old_versions/README.md', 'testsuite/README.md', '.github/FUNDING.yml', 'rsync-web/.gitignore',
    'rsync-web/rsync-and-debian/.cvsignore',
}
WEBSITE_SUFFIXES = {'.html', '.css', '.txt', '.jpg', '.gif', '.xcf', '.sgml', '.tex', '.aux', '.log'}
FULL_CHECK_FILES = {
    '.gitattributes', '.gitignore', 'Doxyfile', 'cmd-or-msg', 'config.guess',
    'config.sub', 'configure', 'daemon-parm.txt', 'install-sh', 'maybe-make-man',
    'md-convert', 'md2man', 'mkgitver', 'prepare-source', 'rsync-ssl', 'wildtest.txt',
    'rsync-web/bin/badge-update', 'rsync-web/bin/upload', 'rsync-web/convert-gpl',
    'rsync-web/filt', 'rsync-web/rsync-debug', 'rsync-web/rsync_wrapper.pl',
    'rsync-web/tech_report/images.pl', 'rsync-web/tech_report/labels.pl',
}
FULL_CHECK_DIRS = ('lib/', 'm4/', 'packaging/', 'popt/', 'support/', 'zlib/')
SOURCE_SUFFIXES = {'.c', '.h', '.cpp', '.S', '.awk', '.in', '.ac', '.mak'}


def empty():
    return {name: False for name in OUTPUTS}


def enable(selected, *names):
    for name in names:
        selected[name] = True


def full():
    return {name: True for name in OUTPUTS}


def no_tests(path):
    return (path in NO_TEST_FILES
            or path.startswith('rsync-web/') and PurePosixPath(path).suffix in WEBSITE_SUFFIXES)


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
        raise ValueError('Unclassified changed path: ' + repr('.github/workflows/' + name))
    enable(selected, *mapping[name])


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
        raise ValueError('Unclassified changed path: ' + repr('.github/scripts/' + name))


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
    selected = empty()
    for raw in paths:
        path = raw.replace('\\', '/')
        parsed = PurePosixPath(path)
        if not path or parsed.is_absolute() or '..' in parsed.parts:
            raise ValueError('Invalid changed path')
        if path == 'rsync.1.md':
            enable(selected, *OUTPUTS)
        elif path in ARCHIVE_DOCUMENTS:
            enable(selected, 'archive', 'diagnostics')
        elif no_tests(path):
            continue
        elif path.startswith('testsuite/'):
            testsuite(selected, path)
        elif path.startswith('old_versions/'):
            enable(selected, 'versions', 'diagnostics')
        elif parsed.parent == PurePosixPath('.github/workflows'):
            workflow(selected, parsed.name)
        elif parsed.parent == PurePosixPath('.github/scripts'):
            script(selected, parsed.name)
        elif path.startswith('.github/actions/interoperability/'):
            enable(selected, 'interoperability', 'diagnostics')
        elif (path in FULL_CHECK_FILES or path.startswith(FULL_CHECK_DIRS)
              or len(parsed.parts) == 1 and parsed.suffix in SOURCE_SUFFIXES):
            enable(selected, *OUTPUTS)
        else:
            raise ValueError('Unclassified changed path: ' + repr(path))
    return selected


def changed_paths(event, event_name):
    if not isinstance(event, dict):
        raise ValueError('Invalid CI event')
    if event_name == 'pull_request':
        request = event.get('pull_request')
        if not isinstance(request, dict):
            raise ValueError('Pull request event has no request')
        base = request.get('base', {}).get('sha', '')
        head = request.get('head', {}).get('sha', '')
        separator = '...'
    elif event_name == 'push':
        base, head = event.get('before', ''), event.get('after', '')
        separator = '..'
    else:
        raise ValueError('Unsupported CI event')
    if (not isinstance(base, str) or not isinstance(head, str)
            or not re.fullmatch(r'[0-9a-f]{40}', base) or not re.fullmatch(r'[0-9a-f]{40}', head)
            or head == '0' * 40 or event_name == 'pull_request' and base == '0' * 40):
        raise ValueError('Invalid change revisions')
    if base == '0' * 40:
        command = ['git', 'ls-tree', '--name-only', '-r', '-z', head]
    else:
        command = ['git', 'diff', '--name-only', '--no-renames', '-z', '--diff-filter=ACDMRTUXB',
                   base + separator + head]
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError('Cannot determine changed paths')
    paths = [os.fsdecode(value) for value in result.stdout.split(b'\0') if value]
    if len(paths) > 10000:
        raise ValueError('Changed path count exceeds limit')
    return paths


def main():
    event_name = os.environ.get('GITHUB_EVENT_NAME')
    if event_name in ('pull_request', 'push'):
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        selected = select(changed_paths(event, event_name))
    elif event_name in ('schedule', 'workflow_dispatch'):
        selected = full()
    else:
        raise ValueError('Unsupported CI event')
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
