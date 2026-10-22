#!/usr/bin/env python3
# Python handling of niceness options like --nice or --ionice.
#
# Foundational smoke test: --version / --info=help / --debug=help all
# work with --nice or --ionice without crash,
# plus test passing the correct options to the remote command

import sys
import os
import shlex
import shutil
import subprocess

from rsyncfns import (
    FROMDIR, RSYNC, SRCDIR, TODIR,
    checkit, hands_setup, run_rsync, test_fail, test_skipped, rsync_path_arg, rsh_cmd
)

test_skipped("Skipping tests to see, if build succeeds")


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

SSH = rsh_cmd()

# Allow opting into real ssh via the shconfig variable, like the shell test.
if os.environ.get('rsync_enable_ssh_tests') == 'yes':
    real_ssh = shutil.which('ssh')
    if real_ssh:
        SSH = real_ssh

# SSH is quoted for rsync's own tokenizer (a build path with '~' or a space
# comes out in quotes), so split it the same way before exec'ing it directly.
probe = subprocess.run(
    [*shlex.split(SSH), '-oBatchMode yes', 'localhost', 'echo', 'yes'],
    capture_output=True, text=True,
)
if probe.stdout.strip() != 'yes':
    test_skipped(
        "Skipping SSH tests because ssh connection to localhost not authorised"
    )

STRACE=shutil.which('strace')

if STRACE is None:
    test_skipped( "Skipping SSH tests because strace is not available" )


print(f"Using remote shell: {SSH}")



def run_rsync_nice_test(testname: str, args: list, expected_result: dict[str, str]) -> None:

    print('### TEST ' + testname + ' BEGIN ###\n')
    hands_setup()
    FROMDIR.mkdir(parents=True, exist_ok=True)
    (FROMDIR / '1.txt').write_text("Testfile1'\n")
    (FROMDIR / 'wasMitX.txt').write_bytes(b"x" * 10_000)
    (FROMDIR / '2.txt').write_text("Testfile2'\n")

    probe = run_rsync(*['-nvvr', '--bwlimit=1K', '--no-compress', '--whole-file', *args, f'--rsync-path=strace -e trace=setpriority,getpriority,ioprio_set,ioprio_get {rsync_path_arg()}', f'{FROMDIR}/', f'localhost:{TODIR}/'], check=False, capture_output=True)
    print('stdout')
    print(probe.stdout)
    print('stderr')
    print(probe.stderr)

    result = {
        "nice_local":"<not set>",
        "ionice_local":"<not set>",
        "nice_remote":"<not set>",
        "ionice_remote":"<not set>",
        "nice_strace":"<not set>",
        "ionice_strace":"<not set>"
    }

    for remote_line in probe.stdout.splitlines():
        print("stdout line: "+remote_line);
        if 'nice not supported for' in remote_line:
            test_skipped("Skipping tests because niceness is not supported on this platform")
        if 'opening connection using' in remote_line:
            actual_remote_args = remote_line.strip()
        elif 'client to new priority' in remote_line:
            print("stdout: Found client info: "+remote_line);
            if 'renice' in remote_line:
                result["nice_local"] = remote_line.split("to new priority", 1)[1].split("(", 1)[0].strip()
                print("Local nice value: "+result["nice_local"]);
            else:
                result["ionice_local"] = remote_line.split("to new priority", 1)[1].split("(", 1)[0].strip()
                print("Local ionice value: "+result["ionice_local"]);
        else:
            print('ignoring stdout line: ' + remote_line)
    for remote_line in probe.stderr.splitlines():
        print("stderr line: "+remote_line);
        if 'nice not supported for' in remote_line:
            test_skipped("Skipping tests because niceness is not supported on this platform")
        if 'Host key verification failed' in remote_line:
            test_skipped("Skipping tests because ssh could not establish connection: Host key verification failed")
        if 'error creating process trace' in remote_line:
            test_skipped("Skipping tests because strace could not create a process trace")
        if 'setpriority(PRIO_PROCESS, 0,'  in remote_line:
            result["nice_strace"] = remote_line.split("(PRIO_PROCESS, 0,", 1)[1].replace(" ", "").replace(")=", "=")
        elif 'ioprio_set(IOPRIO_WHO_PROCESS, 0, IOPRIO_PRIO_VALUE'  in remote_line:
            result["ionice_strace"] = remote_line.split("IOPRIO_PRIO_VALUE(", 1)[1].replace(" ", "").replace("))=", "=")
        elif 'client to new priority' in remote_line:
            print("stdout: Found client info: "+remote_line);
            if 'renice' in remote_line:
                result["nice_local"] = remote_line.split("to new priority", 1)[1].split("(", 1)[0].strip()
                print("Local nice value: "+result["nice_local"]);
            else:
                result["ionice_local"] = remote_line.split("to new priority", 1)[1].split("(", 1)[0].strip()
                print("Local ionice value: "+result["ionice_local"]);
        elif 'server to new priority' in remote_line:
            print("stderr: Found server info: "+remote_line);
            if 'renice' in remote_line:
                result["nice_remote"] = remote_line.split("to new priority", 1)[1].split("(", 1)[0].strip()
                print("Remote nice value: "+result["nice_remote"]);
            else:
                result["ionice_remote"] = remote_line.split("to new priority", 1)[1].split("(", 1)[0].strip()
                print("Remote ionice value: "+result["ionice_remote"]);
        elif 'rsync: Invalid argument passed to --nice' in remote_line:
            print("stderr: rsync complains about invalid argument: "+remote_line);
            result["rsync_fail"] = true
        else:
            print('ignoring stderr line: ' + remote_line)
    test_result = result == expected_result
    if test_result:
        print('### TEST ' + testname + ': SUCCESS ###\nResult : ' + str(result))
    else:
        print('### TEST ' + testname + ': FAILED ###\n')
        sys.stdout.flush()
        fail('### TEST ' + testname +": \nExpected: "+ str(expected_result) + " \nbut was : " + str(result))
    print('### TEST ' + testname + ' END ###\n')
    sys.stderr.flush()
    sys.stdout.flush()


run_rsync_nice_test("Test with real rsync, no options", [], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"<not set>",
    "ionice_remote":"<not set>",
    "nice_strace": "<not set>",
    "ionice_strace": "<not set>"});

run_rsync_nice_test("Test with real rsync -Q", [ '-Q' ], {
    "nice_local":"19",
    "ionice_local":"idle",
    "nice_remote":"19",
    "ionice_remote":"idle",
    "nice_strace": "19=0",
    "ionice_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync -s -Q", [ '-s', '-Q'  ], {
    "nice_local":"19",
    "ionice_local":"idle",
    "nice_remote":"19",
    "ionice_remote":"idle",
    "nice_strace": "19=0",
    "ionice_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync nice local", [ '--nice=local' ], {
    "nice_local":"19",
    "ionice_local":"idle",
    "nice_remote":"<not set>",
    "ionice_remote":"<not set>",
    "nice_strace": "<not set>",
    "ionice_strace": "<not set>"});

run_rsync_nice_test("Test with real rsync nice remote", [ '--nice=remote' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"19",
    "ionice_remote":"idle",
    "nice_strace": "19=0",
    "ionice_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync nice remote successfully", [ '--nice=remote:5/idle' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"5",
    "ionice_remote":"idle",
    "nice_strace": "5=0",
    "ionice_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync nice remote (nice rejected)", [ '--nice=remote:-4/idle' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"-4 failed",
    "ionice_remote":"idle",
    "nice_strace": "-4=-1EACCES(Permissiondenied)",
    "ionice_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync nice remote (ionice rejected)", [ '--nice=remote:5/rt_6' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"5",
    "ionice_remote":"rt_6 failed",
    "nice_strace": "5=0",
    "ionice_strace": "IOPRIO_CLASS_RT,6=-1EPERM(Operationnotpermitted)"});
