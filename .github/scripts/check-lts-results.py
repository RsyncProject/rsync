import json
import os
import sys


EXPECTED = {
    'almalinux',
    'alpine_x86',
    'cygwin',
    'diagnostics',
    'freebsd',
    'linux',
    'macos',
    'mixed_versions',
    'netbsd',
    'openbsd',
    'solaris',
}


def check(data):
    if not isinstance(data, dict) or set(data) != EXPECTED:
        raise ValueError('Missing or unexpected LTS job families')

    results = {}
    for name in sorted(EXPECTED):
        job = data[name]
        if not isinstance(job, dict):
            raise ValueError(f'{name}: invalid result')
        result = job.get('result')
        if result != 'success':
            raise ValueError(f'{name}: {result or "missing result"}')
        results[name] = result
    return results


def main():
    try:
        results = check(json.loads(os.environ['LTS_RESULTS']))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f'LTS result check failed: {error}', file=sys.stderr)
        return 1
    print('\n'.join(f'{name}: {result}' for name, result in results.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
