#!/usr/bin/env python3
# Python handling of niceness options like --nice or --ionice.
#
# Foundational smoke test: --version / --info=help / --debug=help all
# work with --nice or --ionice without crash,
# plus test establishing a remote transfer, stracing local and remote process to check applied priorities

import sys
import os
import shlex
import shutil
import subprocess

from rsyncfns import (
    FROMDIR, RSYNC, SRCDIR, TODIR,
    checkit, hands_setup, run_rsync, test_fail, test_skipped, rsync_path_arg, rsh_cmd
)

# Helper function
def fail(msg: str) -> 'None':
    print('### TEST FAILURE ### ' + msg + '\n')
    test_fail(msg)

def skip(msg: str) -> 'None':
    print('### TEST SKIPPED ### ' + msg + '\n')
    test_skipped(msg)
    raise SystemExit(0)

if sys.platform.startswith(('solaris', 'Solaris')):
	skip("niceness-ssh-strace test not supported on " + sys.platform)

print(f"Now running the tests on platform: {sys.platform}")

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
    skip("Skipping SSH tests because ssh connection to localhost not authorised")

STRACE=shutil.which('strace')

if STRACE is None:
    skip("Skipping SSH tests because strace is not available")


print(f"Using remote shell: {SSH}")



def split_strace_rsync_cmd(cmd: str) -> list:
    """Split an rsync command string into argv, tolerating spaces in the path.

    RSYNC may be a wrapper command ('valgrind --tool=memcheck /build/rsync'),
    which has to be split, or a plain path to the binary, which must not be if
    it contains a space -- shlex.split() would turn '/ws test/rsync' into two
    nonexistent programs.  A path that exists is one word by definition, so
    check that first and only fall back to splitting for a real command line.

    Call this at use time, never once at import: tests such as chown-fake
    append ' --fake-super' to rsyncfns.RSYNC part-way through, and a cached
    split would keep handing back the pre-mutation command.

    Here we support only calling rsync with strace, otherwise skip tests.
    """
    if os.path.isfile(cmd):
        return ['strace', '-e', 'trace=setpriority,getpriority,ioprio_set,ioprio_get', cmd]
    # The path may be followed by options -- chown-fake and friends append
    # ' --fake-super' to RSYNC -- so the whole string is no longer a filename.
    # Take the longest leading run that names an existing file as the program
    # and split only what follows.
    for m in reversed(list(re.finditer(r'\s+', cmd))):
        head = cmd[:m.start()]
        if os.path.isfile(head):
            return ['strace', '-e', 'trace=setpriority,getpriority,ioprio_set,ioprio_get', head] + shlex.split(cmd[m.start():])
        skip("Skipping SSH tests because strace is not available")
    return shlex.split(cmd)



def strace_rsync_argv(*args: str) -> list:
    """Return the argv for invoking strace rsync with the given extra arguments.

    RSYNC may be a multi-word command (e.g. 'valgrind ... /build/rsync'); we
    shlex-split it so subprocess sees a proper argv list. Each *args entry
    is appended verbatim, so callers should pass tokens already split (no
    embedded option/value joined by spaces).
    """
    return split_strace_rsync_cmd(RSYNC) + list(args)



def run_strace_rsync(*args: str, check: bool = True,
              capture_output: bool = False) -> subprocess.CompletedProcess:
    """Run strace rsync with the given arguments.

    By default, stdout/stderr inherit (so the runner captures them in the
    per-test log). Set capture_output=True if the test needs to inspect the
    output. If check is True (the default), a non-zero exit calls
    test_fail() with the rsync command line.
    """
    argv = strace_rsync_argv(*args)
    if capture_output:
        proc = subprocess.run(argv, capture_output=True, text=True)
    else:
        proc = subprocess.run(argv)
    if check and proc.returncode != 0:
        test_fail(f"rsync exited {proc.returncode}: {' '.join(argv)}")
    return proc


def run_rsync_local_test(testname: str, args: list, expected: dict[str, str]) -> None:

    print('### TEST ' + testname + ' BEGIN ###\n')
    hands_setup()
    FROMDIR.mkdir(parents=True, exist_ok=True)
    (FROMDIR / '1.txt').write_text("Testfile1'\n")
    (FROMDIR / 'wasMitX.txt').write_bytes(b"x" * 10_000)
    (FROMDIR / '2.txt').write_text("Testfile2'\n")

    probe = run_strace_rsync(*['-nvvr', '--bwlimit=1K', '--no-compress', '--whole-file', *args, f'--rsync-path={rsync_path_arg()}', f'{FROMDIR}/', f'localhost:{TODIR}/'], check=False, capture_output=True)
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

    result = {
        "nice_local":"<not set>",
        "ionice_local":"<not set>",
        "nice_remote":"<not set>",
        "ionice_remote":"<not set>",
        "nice_strace":"<not set>",
        "ionice_strace":"<not set>"
    }
    
    expected_result = {key: expected[key] for key in ["nice_local", "ionice_local", "nice_remote", "ionice_remote"] if key in expected}
    expected_result["nice_strace"] = expected["nice_local_strace"]
    expected_result["ionice_strace"] = expected["ionice_local_strace"]

    for remote_line in probe.stdout.splitlines():
        print("stdout line: "+remote_line);
        if 'nice not supported for' in remote_line:
            skip("Skipping tests because niceness is not supported on this platform")
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
            skip("Skipping tests because niceness is not supported on this platform")
        if 'Host key verification failed' in remote_line:
            skip("Skipping tests because ssh could not establish connection: Host key verification failed")
        if 'error creating process trace' in remote_line:
            skip("Skipping tests because strace could not create a process trace")
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

def run_rsync_remote_test(testname: str, args: list, expected: dict[str, str]) -> None:

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
    
    expected_result = {key: expected[key] for key in ["nice_local", "ionice_local", "nice_remote", "ionice_remote"] if key in expected}
    expected_result["nice_strace"] = expected["nice_remote_strace"]
    expected_result["ionice_strace"] = expected["ionice_remote_strace"]

    for remote_line in probe.stdout.splitlines():
        print("stdout line: "+remote_line);
        if 'nice not supported for' in remote_line:
            skip("Skipping tests because niceness is not supported on this platform")
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
            skip("Skipping tests because niceness is not supported on this platform")
        if 'Host key verification failed' in remote_line:
            skip("Skipping tests because ssh could not establish connection: Host key verification failed")
        if 'error creating process trace' in remote_line:
            skip("Skipping tests because strace could not create a process trace")
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


def run_rsync_nice_test(testname: str, args: list, expected_result: dict[str, str]) -> None:
	run_rsync_local_test(testname+' (local  strace)', args, expected_result)
	run_rsync_remote_test(testname+' (remote strace)', args, expected_result)

run_rsync_nice_test("Test with real rsync, no options", [], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"<not set>",
    "ionice_remote":"<not set>",
    "nice_local_strace": "<not set>",
    "ionice_local_strace": "<not set>",
    "nice_remote_strace": "<not set>",
    "ionice_remote_strace": "<not set>"});

run_rsync_nice_test("Test with real rsync -Q", [ '-Q' ], {
    "nice_local":"19",
    "ionice_local":"idle",
    "nice_remote":"19",
    "ionice_remote":"idle",
    "nice_local_strace": "19=0",
    "ionice_local_strace": "IOPRIO_CLASS_IDLE,0=0",
    "nice_remote_strace": "19=0",
    "ionice_remote_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync -s -Q", [ '-s', '-Q'  ], {
    "nice_local":"19",
    "ionice_local":"idle",
    "nice_remote":"19",
    "ionice_remote":"idle",
    "nice_local_strace": "19=0",
    "ionice_local_strace": "IOPRIO_CLASS_IDLE,0=0",
    "nice_remote_strace": "19=0",
    "ionice_remote_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync nice local", [ '--nice=local' ], {
    "nice_local":"19",
    "ionice_local":"idle",
    "nice_remote":"<not set>",
    "ionice_remote":"<not set>",
    "nice_local_strace": "19=0",
    "ionice_local_strace": "IOPRIO_CLASS_IDLE,0=0",
    "nice_remote_strace": "<not set>",
    "ionice_remote_strace": "<not set>"});

run_rsync_nice_test("Test with real rsync nice remote", [ '--nice=remote' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"19",
    "ionice_remote":"idle",
    "nice_local_strace": "<not set>",
    "ionice_local_strace": "<not set>",
    "nice_remote_strace": "19=0",
    "ionice_remote_strace": "IOPRIO_CLASS_IDLE,0=0"});

run_rsync_nice_test("Test with real rsync nice local successfully", [ '--nice=local:5/be_6' ], {
    "nice_local":"5",
    "ionice_local":"be_6",
    "nice_remote":"<not set>",
    "ionice_remote":"<not set>",
    "nice_local_strace": "5=0",
    "ionice_local_strace": "IOPRIO_CLASS_BE,6=0",
    "nice_remote_strace": "<not set>",
    "ionice_remote_strace": "<not set>"});

run_rsync_nice_test("Test with real rsync nice remote successfully", [ '--nice=remote:5/be_6' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"5",
    "ionice_remote":"be_6",
    "nice_local_strace": "<not set>",
    "ionice_local_strace": "<not set>",
    "nice_remote_strace": "5=0",
    "ionice_remote_strace": "IOPRIO_CLASS_BE,6=0"});

run_rsync_nice_test("Test with real rsync nice local+remote successfully", [ '--nice=local:1/be_2,remote:5/be_6' ], {
    "nice_local":"1",
    "ionice_local":"be_2",
    "nice_remote":"5",
    "ionice_remote":"be_6",
    "nice_local_strace": "1=0",
    "ionice_local_strace": "IOPRIO_CLASS_BE,2=0",
    "nice_remote_strace": "5=0",
    "ionice_remote_strace": "IOPRIO_CLASS_BE,6=0"});

run_rsync_nice_test("Test with real rsync nice local+remote successfully with -s ", [ '--nice=local:1/be_2,remote:5/be_6', '-s' ], {
    "nice_local":"1",
    "ionice_local":"be_2",
    "nice_remote":"5",
    "ionice_remote":"be_6",
    "nice_local_strace": "1=0",
    "ionice_local_strace": "IOPRIO_CLASS_BE,2=0",
    "nice_remote_strace": "5=0",
    "ionice_remote_strace": "IOPRIO_CLASS_BE,6=0"});

run_rsync_nice_test("Test with real rsync nice local (nice rejected)", [ '--nice=local:-4/idle' ], {
    "nice_local":"-4 failed",
    "ionice_local":"idle",
    "nice_remote":"<not set>",
    "ionice_remote":"<not set>",
    "nice_local_strace": "-4=-1EACCES(Permissiondenied)",
    "ionice_local_strace": "IOPRIO_CLASS_IDLE,0=0",
    "nice_remote_strace": "<not set>",
    "ionice_remote_strace": "<not set>"});

run_rsync_nice_test("Test with real rsync nice remote (nice rejected)", [ '--nice=remote:-4/be_0' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"-4 failed",
    "ionice_remote":"be_0",
    "nice_local_strace": "<not set>",
    "ionice_local_strace": "<not set>",
    "nice_remote_strace": "-4=-1EACCES(Permissiondenied)",
    "ionice_remote_strace": "IOPRIO_CLASS_BE,0=0"});

run_rsync_nice_test("Test with real rsync nice local (ionice rejected)", [ '--nice=local:5/rt_6' ], {
    "nice_local":"5",
    "ionice_local":"rt_6 failed",
    "nice_remote":"<not set>",
    "ionice_remote":"<not set>",
    "nice_local_strace": "5=0",
    "ionice_local_strace": "IOPRIO_CLASS_RT,6=-1EPERM(Operationnotpermitted)",
    "nice_remote_strace": "<not set>",
    "ionice_remote_strace": "<not set>"});

run_rsync_nice_test("Test with real rsync nice remote (ionice rejected)", [ '--nice=remote:5/rt_6' ], {
    "nice_local":"<not set>",
    "ionice_local":"<not set>",
    "nice_remote":"5",
    "ionice_remote":"rt_6 failed",
    "nice_local_strace": "<not set>",
    "ionice_local_strace": "<not set>",
    "nice_remote_strace": "5=0",
    "ionice_remote_strace": "IOPRIO_CLASS_RT,6=-1EPERM(Operationnotpermitted)"});

run_rsync_nice_test("Test with real rsync nice local+remote (all rejected)", [ '--nice=local:-2/rt_3,remote:-4/rt_5' ], {
    "nice_local":"-2 failed",
    "ionice_local":"rt_3 failed",
    "nice_remote":"-4 failed",
    "ionice_remote":"rt_5 failed",
    "nice_local_strace": "-2=-1EACCES(Permissiondenied)",
    "ionice_local_strace": "IOPRIO_CLASS_RT,3=-1EPERM(Operationnotpermitted)",
    "nice_remote_strace": "-4=-1EACCES(Permissiondenied)",
    "ionice_remote_strace": "IOPRIO_CLASS_RT,5=-1EPERM(Operationnotpermitted)"});

run_rsync_nice_test("Test with real rsync nice local+remote (all rejected, -s)", [ '--nice=local:-2/rt_3,remote:-4/rt_5', '-s' ], {
    "nice_local":"-2 failed",
    "ionice_local":"rt_3 failed",
    "nice_remote":"-4 failed",
    "ionice_remote":"rt_5 failed",
    "nice_local_strace": "-2=-1EACCES(Permissiondenied)",
    "ionice_local_strace": "IOPRIO_CLASS_RT,3=-1EPERM(Operationnotpermitted)",
    "nice_remote_strace": "-4=-1EACCES(Permissiondenied)",
    "ionice_remote_strace": "IOPRIO_CLASS_RT,5=-1EPERM(Operationnotpermitted)"});
