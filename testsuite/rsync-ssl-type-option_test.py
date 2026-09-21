#!/usr/bin/env python3
# rsync-ssl only recognized --type=SSL_TYPE as the FIRST argument, so
# "rsync-ssl --dry-run --type=stunnel host::mod" passed the option through to
# the underlying rsync, which rejected it with "--type=stunnel: unknown option".
# Fix: scan the whole argument list for --type=..., export RSYNC_SSL_TYPE, and
# drop the option before handing the remaining args to rsync.
# A `--` arg stops the wrapper-option scan: `--` and everything after it are
# passed through to rsync verbatim, so an operand such as `--type=stunnel`
# that was protected from option parsing is not consumed by the wrapper.
#
# A fake rsync in PATH records the args it receives and the RSYNC_SSL_TYPE it
# observes; rsync-ssl is run in its normal (non-HELPER) mode with --type= in
# various positions.  The recorded args must contain every other option but
# never a --type= token, RSYNC_SSL_TYPE must match what the wrapper consumed,
# and rsync-ssl must exit successfully.

import os
import subprocess

from rsyncfns import SCRATCHDIR, SRCDIR, rmtree, test_fail

base = SCRATCHDIR / 'rsync-ssl-type-opt'
rmtree(base)
base.mkdir(parents=True)

args_capture = base / 'rsync_args'
type_capture = base / 'rsync_ssl_type'
fakebin = base / 'bin'
fakebin.mkdir(parents=True)
fake_rsync = fakebin / 'rsync'
fake_rsync.write_text(
    f"#!/usr/bin/env bash\n"
    f"printf '%s\\n' \"$@\" > {args_capture}\n"
    f"printf '%s\\n' \"${{RSYNC_SSL_TYPE-UNSET}}\" > {type_capture}\n"
    f"exit 0\n")
fake_rsync.chmod(0o755)

env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ.get('PATH', '')}
for v in ('RSYNC_SSL_TYPE', 'RSYNC_SSL_OPENSSL', 'RSYNC_SSL_STUNNEL'):
    env.pop(v, None)


def run(args, expect_type):
    for capture in (args_capture, type_capture):
        if capture.exists():
            capture.unlink()
    proc = subprocess.run(['bash', str(SRCDIR / 'rsync-ssl')] + args, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        test_fail(f"rsync-ssl exited {proc.returncode} for args {args!r}:\n{proc.stdout}")
    got_args = args_capture.read_text().splitlines() if args_capture.exists() else []
    got_type = type_capture.read_text().strip() if type_capture.exists() else 'UNSET'
    if got_type != expect_type:
        test_fail(f"RSYNC_SSL_TYPE is {got_type!r}, expected {expect_type!r} "
                  f"for args {args!r}:\n{proc.stdout}")
    return got_args


# --- The reported failure: --type= in the middle of the rsync args.
got = run(['--dry-run', '--type=stunnel', 'host::mod'], 'stunnel')
if any(a.startswith('--type=') for a in got):
    test_fail(f"--type= was passed through to rsync instead of being consumed:\n{got}")
for want in ('--dry-run', 'host::mod'):
    if want not in got:
        test_fail(f"missing rsync arg {want!r} after --type= handling:\n{got}")
if not any(a.startswith('--rsh=') for a in got):
    test_fail(f"missing the --rsh= helper option:\n{got}")

# --- First, last, and no --type= keep working.
for pos_args, expect_type in ((['--type=stunnel', '--dry-run', 'host::mod'], 'stunnel'),
                              (['-av', 'host::mod', '--type=openssl'], 'openssl'),
                              (['-av', 'host::mod'], 'UNSET')):
    got = run(pos_args, expect_type)
    if any(a.startswith('--type=') for a in got):
        test_fail(f"--type= was passed through for args {pos_args!r}:\n{got}")
    for want in pos_args:
        if want.startswith('--type='):
            continue
        if want not in got:
            test_fail(f"missing rsync arg {want!r} for args {pos_args!r}:\n{got}")

# --- `--` stops the wrapper-option scan: the protected operand is preserved.
rsh_arg = "--rsh='{}' --HELPER".format(SRCDIR / 'rsync-ssl')
got = run(['--', '--type=stunnel', 'host::mod'], 'UNSET')
if got != [rsh_arg, '--', '--type=stunnel', 'host::mod']:
    test_fail(f"args after -- must be preserved verbatim (no --type= consumed):\n{got}")

# --- A wrapper option before `--` is still consumed; the protected one is not.
got = run(['--type=openssl', '--', '--type=stunnel', 'host::mod'], 'openssl')
if got != [rsh_arg, '--', '--type=stunnel', 'host::mod']:
    test_fail(f"--type= before -- is consumed, args after -- are preserved:\n{got}")

print("rsync-ssl-type-option: --type=SSL_TYPE is consumed in any argument "
      "position (until a -- stops the wrapper-option scan), exported as "
      "RSYNC_SSL_TYPE, and rsync-ssl exits successfully")
