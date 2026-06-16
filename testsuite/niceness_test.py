#!/usr/bin/env python3
# Python handling of niceness options like --nice or --ionice.
#
# Foundational smoke test: --version / --info=help / --debug=help all
# work with --nice or --ionice without crash, 
# plus test passing the correct options to the remote command

import os
import platform

from rsyncfns import (
    FROMDIR, RSYNC, SRCDIR, TODIR,
    checkit, run_rsync, test_fail, test_skipped
)

if platform.system() in ('OpenBSD'):
    test_skipped(
        f"test fails with 'ssh exited with code 1' instead of ignoring the expected error."
        f"support not available on {platform.system()}"
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

# We just want to test which parameters are handed over to the remote,
# but do not really want to run rsync on the remote site to copy files.
# For this we add "--rsh=echo" to replace ssh for the remote site with echo.
# Thus no remote connection is actually created, instead the echo will just
# exit quickly without any further rsync communication, letting the local
# rsync process fail fast, not wasting much time for the test execution.
# It is expected that the local rsync process will exit here with non zero.
# We then just check the log output here for any options that may or may not
# have been given for the remote rsync process by our local rsync process.
def test_remote_args(testname: str, args: list, expected_nice_value: str, expected_ionice_value: str, expected_remote_args: list) -> None:
    print('### TEST BEGIN ### ' + testname + '\n')
    probe = run_rsync(*['-nvv', '--rsh=echo', *args], check=False, capture_output=True)
    if probe.returncode == 0:
        fail('The command should not succeed here.')
    actual_remote_args = 'not found'
    nice_value="<not set>"
    ionice_value="<not set>"
    for remote_line in probe.stdout.splitlines():
        if 'opening connection using' in remote_line:
            actual_remote_args = remote_line.strip()
        elif 'client to new priority' in remote_line:
            print("stdout: Found client info: "+remote_line);
            if 'renice' in remote_line:
                nice_value = remote_line.split("to new priority", 1)[1].strip()
                print("Local nice value: "+nice_value);
            else:
                ionice_value = remote_line.split("to new priority", 1)[1].strip()
                print("Local ionice value: "+ionice_value);
        else:
            print('ignoring line: ' + remote_line)
    for remote_line in probe.stderr.splitlines():
        if 'client to new priority' in remote_line:
            print("stderr: Found client info: "+remote_line);
            if 'renice' in remote_line:
                nice_value = remote_line.split("to new priority", 1)[1].split("failed", 1)[0].strip()
                print("Local nice value: "+nice_value);
            else:
                ionice_value = remote_line.split("to new priority", 1)[1].split("failed", 1)[0].strip()
                print("Local ionice value: "+ionice_value);
    if 'not found' == actual_remote_args:
        fail('No remote shell command has been started or it was not logged.')    
    if not nice_value == expected_nice_value:
        fail('Local nice value does not match: expected: '+expected_nice_value+", but was: "+nice_value)
    if not ionice_value == expected_ionice_value:
        fail('Local ionice value does not match: expected: '+expected_ionice_value+", but was: "+ionice_value)
    for string in expected_remote_args:
        if string[0] == '!':
            substring = string[1:]
            if (substring+' ') in actual_remote_args or ('\"'+substring+'\"') in actual_remote_args:
                fail('Remote command line contains unexpected ' + substring + ': ' + actual_remote_args)
        else:
            if not ((string+' ') in actual_remote_args or ('\"'+string+'\"') in actual_remote_args):
                fail('Remote command line did not contain expected ' + string + ': ' + actual_remote_args)
    print('### TEST END ### ' + testname + '\n')

test_remote_args('Check with no flags', [f'localhost:{FROMDIR}', TODIR], "<not set>", "<not set>", ["--server", "--sender", "!--blahblah",
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

test_remote_args('Check with -Q', ['-Q', f'localhost:{FROMDIR}', TODIR], "19", "idle", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:19/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=all', ['--nice=all', f'localhost:{FROMDIR}', TODIR], "19", "idle", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:19/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=local', ['--nice=local', f'localhost:{FROMDIR}', TODIR], "19", "idle", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "!--nice=local:19/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=remote', ['--nice=remote', f'localhost:{FROMDIR}', TODIR], "<not set>", "<not set>", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:19/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=idle', ['--nice=idle', f'localhost:{FROMDIR}', TODIR], "<not set>", "idle", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:0/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=be_2', ['--nice=be_2', f'localhost:{FROMDIR}', TODIR], "<not set>", "be_2", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:0/be_2",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=be_5', ['--nice=be_5', f'localhost:{FROMDIR}', TODIR], "<not set>", "be_5", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:0/be_5",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=rt_3', ['--nice=rt_3', f'localhost:{FROMDIR}', TODIR], "<not set>", "rt_3", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:0/rt_3",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=rt_7', ['--nice=rt_7', f'localhost:{FROMDIR}', TODIR], "<not set>", "rt_7", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:0/rt_7",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=RT_0', ['--nice=RT_0', f'localhost:{FROMDIR}', TODIR], "<not set>", "rt_0", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:0/rt_0",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=none', ['--nice=none', f'localhost:{FROMDIR}', TODIR], "<not set>", "<not set>", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "!--nice=local:0/none",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=NONE', ['--nice=NONE', f'localhost:{FROMDIR}', TODIR], "<not set>", "<not set>", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "!--nice=local:0/none",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=local:idle', ['--nice=local:idle', f'localhost:{FROMDIR}', TODIR], "<not set>", "idle", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "!--nice=local:19/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=remote:idle', ['--nice=remote:idle', f'localhost:{FROMDIR}', TODIR], "<not set>", "<not set>", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:0/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

test_remote_args('Check with --nice=local:4', ['--nice=local:4', f'localhost:{FROMDIR}', TODIR], "4", "<not set>", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "!--nice=local:4",
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

test_remote_args('Check with --nice=remote:6', ['--nice=remote:6', f'localhost:{FROMDIR}', TODIR], "<not set>", "<not set>", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:6",
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


test_remote_args('Check with --nice=6', ['--nice=6', f'localhost:{FROMDIR}', TODIR], "6", "<not set>", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:6",
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


test_remote_args('Check with --nice=-2/IDLE', ['--nice=-2/IDLE', f'localhost:{FROMDIR}', TODIR], "-2", "idle", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:-2/idle",
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

test_remote_args('Check with --nice=ALL:-2/IDLE', ['--nice=ALL:-2/IDLE', f'localhost:{FROMDIR}', TODIR], "-2", "idle", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:-2/idle",
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

test_remote_args('Check with --nice=local:10/BE_0,remote:19/IDLE', ['--nice=local:10/BE_0,remote:19/IDLE', f'localhost:{FROMDIR}', TODIR], "10", "be_0", ["--server", "--sender", "!--blahblah",
                                                                          "!--nice", 
                                                                          "--nice=local:19/idle",
                                                                          "!--nice-local-value=4",
                                                                          "!--nice-remote", 
                                                                          "!--nice-remote-value=6", 
                                                                          "!--ionice", 
                                                                          "!--ionice-local",
                                                                          "!--ionice-remote",
                                                                          "!-Q"
                                                                          ])

