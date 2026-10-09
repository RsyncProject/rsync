#!/usr/bin/env python3
# Regression: a %RSYNC_*% reference in an ordinary daemon string param
# (path/uid/gid/...) must expand to the RAW value, not be shell-quoted.
#
# The %RSYNC_*% shell-escaping (an injection guard for the shell-executed hooks:
# pre/post-xfer exec, early exec, name converter) leaked into the shared
# expand_vars() used by every string accessor.  So `path = /srv/%RSYNC_MODULE_NAME%`
# expanded to /srv/'served' and the documented `path = /home/%RSYNC_USER_NAME%`
# (rsyncd.conf.5) broke with "@ERROR: chdir failed".  Now only the hook
# accessors escape; ordinary params expand verbatim.

import subprocess

from rsyncfns import (
    SCRATCHDIR, rmtree, rsync_argv, start_test_daemon, test_fail,
    write_daemon_conf,
)

DAEMON_PORT = 12906

base = SCRATCHDIR / 'daemon-path-var'
rmtree(base)
# The module's real path is <root>/<module-name>; the conf references it via
# %RSYNC_MODULE_NAME% (set for every connection, no auth needed).  If that
# expands quoted, the daemon chdir's to <root>/'served' (literal quotes), which
# does not exist -> the pull fails.
root = base / 'root'
served = root / 'served'        # module is named "served"
served.mkdir(parents=True)
(served / 'hello.txt').write_text('EXPANDED\n')

conf = write_daemon_conf([
    ('served', {'path': f'{root}/%RSYNC_MODULE_NAME%',
                'use chroot': 'no', 'read only': 'yes'}),
])
url = start_test_daemon(conf, DAEMON_PORT).rstrip('/')

dest = base / 'dest'
dest.mkdir(parents=True)
proc = subprocess.run(
    rsync_argv('-a', f'{url}/served/hello.txt', str(dest) + '/'),
    capture_output=True, text=True)

got = dest / 'hello.txt'
if proc.returncode != 0 or not got.is_file():
    test_fail("%RSYNC_MODULE_NAME% in `path` did not expand to the raw value "
              "(the daemon could not chdir -- the path was shell-quoted); "
              f"rc={proc.returncode}\nstderr: {proc.stderr.strip()}")
if got.read_text() != 'EXPANDED\n':
    test_fail(f"unexpected content from the expanded-path module: {got.read_text()!r}")

print("daemon-path-rsync-var: %RSYNC_*% in a string param expands unquoted")
