#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description='Check required rsync build features')
    parser.add_argument('binary')
    parser.add_argument('features', nargs='+')
    args = parser.parse_args()
    output = subprocess.check_output([args.binary, '-VV'], text=True, timeout=30)
    sys.stdout.write(output)
    version = json.loads(output)
    available = {key for key, value in version['capabilities'].items() if value is True}
    for field in ('checksum_list', 'compress_list'):
        values = version[field]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            parser.exit(1, f'Invalid {field} in build metadata\n')
        available.update(values)
    missing = set(args.features) - available
    if missing:
        parser.exit(1, f'Missing build features: {", ".join(sorted(missing))}\n')

if __name__ == '__main__':
    main()
