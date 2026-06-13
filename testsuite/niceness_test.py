#!/usr/bin/env python3
# Python handling of niceness options like --nice or --ionice.
#
# Foundational smoke test: --version / --info=help / --debug=help all
# work with --nice or --ionice without crash, 
# plus test passing the correct options to the remote command

import os

from rsyncfns import (
    FROMDIR, RSYNC, SRCDIR, TODIR,
    checkit, run_rsync, test_fail,
)

# Helper function
def fail(msg: str) -> 'None':
    print('### TEST FAILURE ### ' + msg + '\n')
    test_fail(msg)

# Basic help dumps must not crash when using any niceness option.
if run_rsync('-Q', '--version', check=False).returncode != 0:
    fail('-Q --version output failed')
if run_rsync('-Q', '--info=help', check=False).returncode != 0:
    fail('-Q --info=help output failed')
if run_rsync('-Q', '--debug=help', check=False).returncode != 0:
    fail('-Q --debug=help output failed')


def test_remote_args(testname: str, args: list, expected_remote_args: list) -> None:
    print('### TEST BEGIN ### ' + testname + '\n')
    probe = run_rsync(*['-nvv', '--rsh=echo', *args], check=False, capture_output=True)
    if probe.returncode == 0:
        fail('The command should not succeed here.')
    actual_remote_args = 'not found'
    for remote_line in probe.stdout.splitlines():
        if 'opening connection using' in remote_line:
            actual_remote_args = remote_line.strip()
        else:
            print('ignoring line: ' + remote_line)
    if 'not found' == actual_remote_args:
        fail('No remote shell command has been started or it was not logged.')    
    for string in expected_remote_args:
        if string[0] == '!':
            substring = string[1:]
            if (substring+' ') in actual_remote_args:
                fail('Remote command line contains unexpected ' + substring + ': ' + actual_remote_args)
        else:
            if not (string+' ') in actual_remote_args:
                fail('Remote command line did not contain expected ' + string + ': ' + actual_remote_args)
    print('### TEST END ### ' + testname + '\n')

test_remote_args('Check with no flags', [f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with -Q', ['-Q', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice', ['--nice', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice-local', ['--nice-local', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice-remote', ['--nice-remote', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --ionice', ['--ionice', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --ionice-local', ['--ionice-local', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --ionice-remote', ['--ionice-remote', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice-local-value=4', ['--nice-local-value=4', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-local-value=6",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=4", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice-remote-value=6', ['--nice-remote-value=6', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "--nice-local-value=6",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=4", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])


test_remote_args('Check with --nice-value=6', ['--nice-value=6', f'localhost:{FROMDIR}', TODIR], ["--server", "--sender", "!--blahblah", 
                                                                          "!--nice", 
                                                                          "!--nice-local",
                                                                          "!--nice-local-value=4",
                                                                          "--nice-local-value=6",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=4", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])



