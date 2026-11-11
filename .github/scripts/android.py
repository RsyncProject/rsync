import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=['test', 'package'])
    parser.add_argument('--executor')
    parser.add_argument('--abi', choices=['arm64-v8a', 'armeabi-v7a', 'x86_64'])
    args = parser.parse_args()
    if args.task == 'test':
        description = subprocess.check_output(['file', 'rsync'], text=True, timeout=30)
        print(description, end='')
        if 'statically linked' not in description or 'dynamically linked' in description:
            raise ValueError('Android binary is not statically linked')
        if not args.executor:
            parser.error('--executor is required for Bionic testing')
        script = Path(__file__).with_name('android-transfer.sh')
        subprocess.run(['sh', script, args.executor, Path('rsync').resolve()],
                       check=True, timeout=180)
        return 0
    if not args.abi:
        parser.error('--abi is required for packaging')
    match = re.search(r'RSYNC_VERSION\s+"([A-Za-z0-9.+-]+)"', Path('version.h').read_text())
    if not match:
        raise ValueError('Cannot determine the rsync version')
    name = f'rsync-{match[1]}-android-{args.abi}'
    output = Path('dist')
    output.mkdir(exist_ok=True)
    binary = output / name
    shutil.copy2('rsync', binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (output / f'{name}.sha256').write_text(f'{digest}  {name}\n')
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        stream.write(f'ARTIFACT_NAME=rsync-android-{args.abi}\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Android check failed: {error}', file=sys.stderr)
        raise SystemExit(1)
