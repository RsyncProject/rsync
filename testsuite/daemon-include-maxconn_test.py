#!/usr/bin/env python3
"""Daemon coverage: rsyncd.conf `&include`/`&merge` directives + `max connections`.

Covers:
  - params.c include_config(): both the file form (`&merge = file.inc`) and
    the directory form (`&include = dir/` -> readdir + glob *.conf + qsort
    via name_cmp() when count>1) -> the ]push/]reset/]pop section-stack path
    in loadparm.c.
  - connection.c claim_connection() / util1.c lock_range(): a module with
    `max connections = N` + `lock file = path` opens the lock file via
    open_no_attacker_symlinks() and lock_range()s a 4-byte slot.
"""

import subprocess

from rsyncfns import (
    SCRATCHDIR, FROMDIR,
    claim_ports, get_rootgid, get_rootuid, get_testuid,
    make_tree, makepath, rmtree, rsync_argv,
    start_test_daemon, test_fail, write_daemon_conf,
)

# `&include` (manage_globals=1) ]push/]pop's the global section stack, so the
# parent conf's root-aware `uid = 0`/`gid = 0` does NOT propagate into the
# fragments -- the included modules would default to `nobody` and fail to
# write into root-owned dest dirs.  Mirror write_daemon_conf()'s handling.
if get_testuid() == get_rootuid():
    UIDGID = f'\tuid = {get_rootuid()}\n\tgid = {get_rootgid()}\n'
else:
    UIDGID = ''

PORT = 12895
claim_ports(PORT)

src = FROMDIR
rmtree(src)
make_tree(src, depth=1)

# A directory of *.conf fragments for `&include = <dir>` (needs >=2 entries
# to hit name_cmp() via qsort, and a non-matching file to exercise the
# wildmatch("*.conf", ...) skip).
conf_d = SCRATCHDIR / 'rsyncd.conf.d'
makepath(conf_d)

dest_a = SCRATCHDIR / 'dest-incA'
dest_b = SCRATCHDIR / 'dest-incB'
dest_m = SCRATCHDIR / 'dest-merge'
makepath(dest_a, dest_b, dest_m)

lockfile = SCRATCHDIR / 'rsyncd.lock'

(conf_d / '20-b.conf').write_text(
    f'[incB]\n\tpath = {dest_b}\n\tread only = no\n{UIDGID}'
)
(conf_d / '10-a.conf').write_text(
    '# fragment loaded via &include directory glob\n'
    f'[incA]\n'
    f'\tpath = {dest_a}\n'
    f'\tread only = no\n'
    f'\tmax connections = 2\n'
    f'\tlock file = {lockfile}\n'
    f'{UIDGID}'
)
(conf_d / 'README').write_text('not matched by *.conf glob\n')

# A single file for `&merge = <file>` (the manage_globals=0 / *.inc branch).
merge_inc = SCRATCHDIR / 'extra.inc'
merge_inc.write_text(
    f'[merged]\n\tpath = {dest_m}\n\tread only = no\n'
)

# Main conf: globals carry both directives. write_daemon_conf() emits
# `globals` as `key = value`, which is exactly the &include/&merge syntax.
conf = write_daemon_conf(
    [],
    globals={
        '&include': str(conf_d),
        '&merge': str(merge_inc),
    },
    name='include-maxconn.conf',
)
url = start_test_daemon(conf, PORT)


def push(module):
    return subprocess.run(
        rsync_argv('-r', f'{src}/', f'{url}{module}/'),
        capture_output=True, text=True,
    )


# --- &include directory: both fragment-defined modules must resolve --------
for mod, dest in (('incA', dest_a), ('incB', dest_b)):
    r = push(mod)
    if r.returncode != 0:
        test_fail(f"&include dir: module [{mod}] should exist "
                  f"(rc={r.returncode}):\n{r.stderr}")
    if not any(dest.iterdir()):
        test_fail(f"&include dir: push to [{mod}] wrote nothing into {dest}")

# --- &merge file: the [merged] module must resolve -------------------------
r = push('merged')
if r.returncode != 0:
    test_fail(f"&merge file: module [merged] should exist "
              f"(rc={r.returncode}):\n{r.stderr}")

# --- max connections: lock file was created and the slot taken+released ----
if not lockfile.exists():
    test_fail(f"max connections=2 should have created lock file {lockfile}")
# Second sequential push to [incA] reuses (or re-acquires) a slot; this
# exercises claim_connection() twice on the same lock file.
r = push('incA')
if r.returncode != 0:
    test_fail(f"second push to [incA] (max connections=2) failed "
              f"(rc={r.returncode}):\n{r.stderr}")

print(f"daemon-include-maxconn: &include={conf_d.name}/ (2 *.conf, qsort), "
      f"&merge={merge_inc.name}, max connections=2 lock file ok")
