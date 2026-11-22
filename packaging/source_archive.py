from pathlib import Path
import shutil
import subprocess
import tempfile


GEN_FILES = [
    'configure.sh', 'aclocal.m4', 'config.h.in',
    'rsync.1', 'rsync.1.html', 'rsync-ssl.1', 'rsync-ssl.1.html',
    'rsyncd.conf.5', 'rsyncd.conf.5.html', 'rrsync.1', 'rrsync.1.html',
]


def make_archive(revision, stage, output):
    stage, output = Path(stage).resolve(), Path(output).resolve()
    if not stage.name.startswith('rsync-') or not stage.is_dir():
        raise ValueError('Invalid release staging directory')
    if {path.name for path in stage.iterdir()} != set(GEN_FILES):
        raise ValueError('Release staging must contain exactly the generated release files')
    for name in GEN_FILES:
        if not (stage / name).is_file() or (stage / name).is_symlink():
            raise ValueError(f'Missing generated release file: {name}')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    selected = subprocess.check_output(['git', 'rev-parse', revision + '^{commit}'], text=True).strip()
    if head != selected:
        raise ValueError('Archive timestamps require the selected revision to be checked out')
    with tempfile.TemporaryDirectory(prefix='rsync-archive-') as directory:
        tracked = Path(directory) / 'tracked.tar'
        subprocess.run(['git', 'archive', '--format=tar', '--prefix=' + stage.name + '/',
                        '--output=' + str(tracked), selected], check=True, timeout=120)
        subprocess.run(['tar', '-C', str(stage.parent), '-xf', str(tracked)], check=True, timeout=120)
        subprocess.run(['support/git-set-file-times', '--quiet', '--prefix=' + str(stage) + '/'],
                       check=True, timeout=120)
        archive = Path(directory) / 'source.tar.gz'
        subprocess.run(['fakeroot', 'tar', '-C', str(stage.parent), '-czf', str(archive),
                        '--exclude=.github', stage.name], check=True, timeout=120)
        shutil.copyfile(archive, output)
