import sys


def check_command(build, binary, profiles, output, transport, *options, checker='testsuite/tools/check.py'):
    if transport not in ('pipe', 'tcp'):
        raise ValueError('Unknown test transport: ' + transport)
    has_pipe = 'pipe' in {profile.strip() for profile in profiles.split(',')}
    if transport == 'pipe' and not has_pipe:
        profiles = 'pipe,' + profiles
    if transport == 'tcp':
        if has_pipe:
            raise ValueError('TCP checks cannot use the pipe profile')
        options += ('--use-tcp', '--daemon-tests-only')
    return [sys.executable, '-S', str(checker), str(build), str(binary), profiles, str(output), *options]
