from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='rsync-install-') as directory:
        root = Path(directory)
        subprocess.run(['make', 'install-all', f'DESTDIR={root}'], check=True)
        required = ['usr/local/bin/rsync', 'usr/local/bin/rsync-ssl', 'usr/local/bin/rrsync',
                    'usr/local/share/man/man1/rsync.1', 'usr/local/share/man/man1/rsync-ssl.1',
                    'usr/local/share/man/man1/rrsync.1', 'usr/local/share/man/man5/rsyncd.conf.5',
                    'etc/stunnel/rsyncd.conf']
        missing = [name for name in required if not (root / name).exists()]
        if missing:
            raise ValueError('Missing installed files: ' + ', '.join(missing))
        subprocess.run(['make', 'uninstall-all', f'DESTDIR={root}'], check=True)
        leftover = [str(path.relative_to(root)) for path in root.rglob('*') if path.is_file() or path.is_symlink()]
        if leftover:
            raise ValueError('Uninstall left files: ' + ', '.join(leftover))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Installation check failed: {error}', file=sys.stderr)
        raise SystemExit(1)
