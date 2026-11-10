import argparse
from pathlib import Path
import platform
import shlex
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('system', choices=['ubuntu', 'almalinux', 'macos', 'cygwin'])
    parser.add_argument('--release')
    parser.add_argument('--arch', required=True)
    args = parser.parse_args()
    print(platform.platform())
    if platform.machine() != args.arch:
        raise ValueError(f'Expected {args.arch}, got {platform.machine()}')
    if args.system in ('ubuntu', 'almalinux'):
        values = {}
        for line in Path('/etc/os-release').read_text().splitlines():
            name, separator, value = line.partition('=')
            if separator and name in ('ID', 'VERSION_ID'):
                parts = shlex.split(value)
                if len(parts) != 1:
                    raise ValueError(f'Invalid {name} in os-release')
                values[name] = parts[0]
        release = values['VERSION_ID']
        if args.system == 'almalinux':
            release = release.split('.')[0]
        if values['ID'] != args.system or release != args.release:
            raise ValueError(f'Unexpected Linux identity: {values}')
    elif args.system == 'macos':
        if platform.system() != 'Darwin' or platform.mac_ver()[0].split('.')[0] != args.release:
            raise ValueError('Unexpected macOS identity')
    elif not platform.system().startswith('CYGWIN'):
        raise ValueError('Expected Cygwin')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError) as error:
        print(f'Platform check failed: {error}', file=sys.stderr)
        raise SystemExit(1)
