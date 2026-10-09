#!/usr/bin/env python3
"""A refuse rule naming one spelling of an option must cover the others.

--del and --delete-during are the same capability written two ways, but the
popt table spells them differently: --del is POPT_ARG_NONE with a destination
(popt stores 1), --delete-during is POPT_ARG_VAL storing 1.  A refuse rule
matched on the raw table fields therefore disabled only the spelling the
administrator happened to write, and the other one still worked.

Both directions are checked, because the mismatch is symmetric: a rule naming
the canonical option was evaded by the short alias, and a rule naming the
alias was evaded by the canonical option.

No raw protocol here, and no custom client: --remote-option (-M) puts the
option in the daemon's argv verbatim, which is exactly the reach an ordinary
user has.
"""

import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

PORT = 12951

base = SCRATCHDIR / 'daemon-refuse-delete-alias'
rmtree(base)
src = base / 'src'
makepath(src)
(src / 'keep').write_text('KEEP\n')

# Two modules, each refusing one spelling of the same capability.
mods = {}
for name, rule in (('refuses_canonical', 'delete-during'), ('refuses_alias', 'del')):
    d = base / name
    makepath(d)
    mods[name] = d

conf = write_daemon_conf([
    ('refuses_canonical', {'path': str(mods['refuses_canonical']), 'read only': 'no',
                           'use chroot': 'no', 'refuse options': 'delete-during'}),
    ('refuses_alias', {'path': str(mods['refuses_alias']), 'read only': 'no',
                       'use chroot': 'no', 'refuse options': 'del'}),
    # No rule at all: proves the pushes themselves work, so a refusal below
    # is the rule firing rather than the transfer being broken.
    ('open', {'path': str(base / 'open'), 'read only': 'no', 'use chroot': 'no'}),
], name='refuse-delete-alias.conf')
makepath(base / 'open')
url = start_test_daemon(conf, PORT)


def push(module, *opts):
    return subprocess.run(
        rsync_argv('-r', *opts, f'{src}/', f'{url}{module}/'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)


def refused(proc, spelling):
    """Did the daemon refuse this exact spelling?

    Requiring the option name, not just the word "refuse", keeps an unrelated
    refusal from standing in for the one under test -- and it doubles as proof
    that -M actually delivered the option to the daemon, which the control
    below cannot show on its own: a silently discarded -M would let the
    transfer succeed just the same."""
    return (proc.returncode != 0
            and f'configured to refuse --{spelling}' in proc.stdout)


# Control: both spellings transfer where nothing refuses them, so a refusal
# below is the rule firing rather than the push being broken.  That -M reached
# the daemon at all is established by the spelling-specific refusals, not here:
# a silently dropped -M would pass this control too.
for opt in ('-M--del', '-M--delete-during'):
    proc = push('open', '--delete', opt)
    if proc.returncode != 0 or not (base / 'open' / 'keep').is_file():
        test_fail(f'control failed: {opt} could not push to a module with no '
                  f'refuse rule (rc={proc.returncode}, '
                  f'output={proc.stdout.strip()[:300]!r})')

# The reported direction: the rule names the canonical option, the client
# sends the alias.
proc = push('refuses_canonical', '--delete', '-M--del')
if not refused(proc, 'del'):
    test_fail('"refuse options = delete-during" did not refuse --del, which '
              'sets the very same delete_during: the rule names a capability, '
              f'not one spelling of it (rc={proc.returncode}, '
              f'output={proc.stdout.strip()[:300]!r})')

# ...and the mirror, since the table shapes differ in both directions.
proc = push('refuses_alias', '--delete', '-M--delete-during')
if not refused(proc, 'delete-during'):
    test_fail('"refuse options = del" did not refuse --delete-during '
              f'(rc={proc.returncode}, output={proc.stdout.strip()[:300]!r})')

# And each rule still refuses the spelling it actually names.
proc = push('refuses_canonical', '--delete', '-M--delete-during')
if not refused(proc, 'delete-during'):
    test_fail('"refuse options = delete-during" did not even refuse '
              f'--delete-during (rc={proc.returncode}, '
              f'output={proc.stdout.strip()[:300]!r})')
proc = push('refuses_alias', '--delete', '-M--del')
if not refused(proc, 'del'):
    test_fail(f'"refuse options = del" did not even refuse --del '
              f'(rc={proc.returncode}, output={proc.stdout.strip()[:300]!r})')

print('a refuse rule for one spelling of --delete-during covers --del too')
