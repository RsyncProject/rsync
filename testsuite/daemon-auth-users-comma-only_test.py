#!/usr/bin/env python3
"""A leading comma in "auth users" must make splitting comma-only.

rsyncd.conf(5) documents that when the auth users value starts with a comma,
splitting is done on commas ALONE, so an entry may contain spaces -- which is
how an NSS group name with a space is written:

    auth users = ,@Group Name:deny, alice:rw

auth_server() ignored that and always tokenised on commas AND whitespace
(strtok(users, " ,\\t")), so "@Group Name:deny" was torn into "@Group" and
"Name:deny": the group rule the administrator wrote never matched anything, and
a username rule they never wrote appeared out of its tail.

The reported impact is an authorization BYPASS: a member of a denied group
evades the deny and matches a later :rw entry.  Both directions are covered.

"grpdeny" drives the reported one without needing an NSS group whose name
contains a space, which a test cannot create: "@[! []*" is a wildmatch class
holding a space that matches any ordinary group name -- one with no "/" in it,
which wildmatch treats as a path separator -- whose first character is neither
a space nor a "[".  So the entry contains a space and still matches a real
group.  The "[" is excluded so that the tail left by a whitespace split,
"[]*", is an unterminated class that can match no name at all: with the
simpler "[! ]*" the tail is "]*", which would match (and deny) any user whose
name began with "]", letting the buggy parser produce the right refusal for
the wrong reason.

"spaced" drives the identical defect in the direction that needs no groups:

    auth users = ,@nosuchgroup alice:deny, alice:rw

  * parsed as documented -- one entry "@nosuchgroup alice:deny" naming a group
    that does not exist, so no match, then "alice:rw" grants access.
  * parsed by splitting on whitespace -- "@nosuchgroup", then "alice:deny",
    which matches the username and refuses the transfer that should succeed.

Either way the rule enforced is not the rule written.  Each direction has its
own control module proving the credential and the transfer itself are fine, and
the deny is checked against the daemon log rather than the client's output,
because a client is told only "auth failed" for every server-side reason alike.
"""

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

PORT = 12949
USER = 'authuser'
PASSWORD = 'known-password'
DATA = 'AUTH-USERS-PARSE\n'

base = SCRATCHDIR / 'auth-users-comma-only'
rmtree(base)
src = base / 'src'
mod = base / 'mod'
ctrl = base / 'ctrl'
grp = base / 'grp'
grpc = base / 'grpc'
makepath(src, mod, ctrl, grp, grpc)
(src / 'f1').write_text(DATA)

# The group half needs an auth name the daemon can resolve to a real uid, or
# getallgroups() finds nothing and no group rule can ever match.
import pwd
REALUSER = pwd.getpwuid(os.geteuid()).pw_name

secrets = base / 'auth-users.secrets'
secrets.write_text(f'{USER}:{PASSWORD}\n{REALUSER}:{PASSWORD}\n')
secrets.chmod(0o600)
pwfile = base / 'auth-users.password'
pwfile.write_text(PASSWORD + '\n')
pwfile.chmod(0o600)

common = {
    'read only': 'no',
    'use chroot': 'no',
    'secrets file': str(secrets),
}

conf = write_daemon_conf([
    # The documented comma-only form.  The first entry names a group that does
    # not exist, so only the second entry may decide the outcome.
    ('spaced', dict(common, path=str(mod),
                    **{'auth users': f',@nosuchgroup {USER}:deny, {USER}:rw'})),
    # Control: same credential, same transfer, no space in any entry.
    ('plain', dict(common, path=str(ctrl),
                   **{'auth users': f'{USER}:rw'})),
    # The reported direction: a group rule containing a space must DENY a member.
    # "[! []*" is a wildmatch class holding a space that matches any ordinary
    # group name whose first character is neither a space nor a "[" -- i.e. a
    # real group of REALUSER -- so the space is exercised without needing an
    # NSS group named with one.  (Ordinary: wildmatch reads "/" as a path
    # separator, so a group name containing one would not match either.)
    #   documented parse: one entry "@[! []*:deny" -> the deny fires
    #   whitespace parse: "@[!" (an unterminated class, so no group) plus
    #                     "[]*:deny" (likewise, so no user), and with neither
    #                     matching the later ":rw" lets them in.
    # Positive control for the group half: "@*" matches any group, so this
    # only succeeds if the daemon resolved REALUSER to a uid, enumerated its
    # groups, accepted its secret and could store the file.  Without it a
    # grpdeny refusal could be any of those failing instead of the deny.
    ('grpctl', dict(common, path=str(grpc),
                    **{'auth users': f'@*:rw'})),
    ('grpdeny', dict(common, path=str(grp),
                     **{'auth users': f',@[! []*:deny, {REALUSER}:rw'})),
], name='auth-users-comma-only.conf')
url = start_test_daemon(conf, PORT)

os.environ['RSYNC_PASSWORD'] = 'wrong-environment-fallback'


def push(module, dest_dir, as_user=USER):
    proc = subprocess.run(
        rsync_argv('-r', f'--password-file={pwfile}', f'{src}/',
                   url.replace('rsync://', f'rsync://{as_user}@', 1) + module + '/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    landed = dest_dir / 'f1'
    return proc, landed


# Control first: if this fails, nothing below means anything.
proc, landed = push('plain', ctrl)
if proc.returncode != 0 or not landed.is_file() or landed.read_text() != DATA:
    test_fail('positive control failed: the credential could not push to a '
              f'module with a plain "auth users" (rc={proc.returncode}, '
              f'output={proc.stdout.strip()[:300]!r})')

proc, landed = push('spaced', mod)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
if not landed.is_file():
    test_fail('"auth users" starting with a comma was split on whitespace: the '
              f'entry "@nosuchgroup {USER}:deny" names a group that does not '
              f'exist and must not match, but its tail was read as a separate '
              f'"{USER}:deny" rule and the transfer was refused ({ctx})')
if landed.read_text() != DATA:
    test_fail(f'transfer was allowed but delivered the wrong content ({ctx})')
if proc.returncode != 0:
    test_fail(f'transfer was allowed but failed ({ctx})')

# Group control: everything the group half depends on must work first.
proc, landed = push('grpctl', grpc, as_user=REALUSER)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
if proc.returncode != 0 or not landed.is_file():
    test_fail(f'group positive control failed: "@*:rw" should admit {REALUSER} '
              'via any of its groups.  Either the daemon could not resolve that '
              'name to a uid, could not enumerate its groups, rejected its '
              f'secret, or could not store the file ({ctx})')

# The reported direction: the spaced group rule must actually DENY.
proc, landed = push('grpdeny', grp, as_user=REALUSER)
ctx = f'rc={proc.returncode}, output={proc.stdout.strip()[:300]!r}'
if landed.is_file() or proc.returncode == 0:
    test_fail('a group deny rule containing a space did not fire: '
              f'"@[! []*:deny" must match a real group of {REALUSER} and '
              'refuse the transfer, but the whitespace split turned it into '
              f'"@[!" and "[]*:deny", neither of which matches, so the later '
              f'"{REALUSER}:rw" granted access ({ctx})')
# ...and it must be refused BY THE DENY.  A client is told only "auth failed"
# whatever the server decided: "denied by rule", "no matching rule" and
# "password mismatch" all reach it identically, so a parse yielding no rules at
# all would satisfy a check on the client output while the deny never matched
# anything.  Only the daemon log names the reason.
logfile = SCRATCHDIR / 'rsyncd.log'
log = logfile.read_text(errors='replace') if logfile.is_file() else ''
if not [ln for ln in log.splitlines()
        if 'auth failed on module grpdeny' in ln
        and f'for {REALUSER}: denied by rule' in ln]:
    test_fail('the transfer was refused, but not by the deny rule: the daemon '
              f'log has no "auth failed on module grpdeny ... for {REALUSER}: '
              'denied by rule".  A refusal for any other reason -- above all '
              '"no matching rule", which is what a parse yielding no rules at '
              'all produces -- would make this case pass without the deny ever '
              f'matching ({ctx}, log={log.strip()[-500:]!r})')

print('a leading comma in "auth users" splits on commas alone')
