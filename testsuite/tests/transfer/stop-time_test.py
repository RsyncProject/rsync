#!/usr/bin/env python3
"""Coverage of --stop-at (options.c parse_time) and --stop-after.

--stop-at parses an absolute y-m-dTh:m time (parse_time): a future time is
accepted and the transfer proceeds; a past time is rejected at parse. --stop-after
takes a minute count. These exercise the OPT_STOP_AT/OPT_STOP_AFTER option
handling that no other test reaches.
"""

from datetime import datetime, timedelta

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, make_tree, rmtree, run_rsync, test_fail, walk_files,
)

src = FROMDIR
rmtree(src)
make_tree(src, depth=2)
rels = [p.relative_to(src) for p in walk_files(src)]

# --- --stop-at in the future: parses, transfer completes normally -----------
# Generated relative to now (a day ahead) rather than a fixed far-future date:
# rsync rejects a --stop-at that isn't strictly in the future, and a hard-coded
# year would date-rot and can overflow a 32-bit time_t.
future = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
rmtree(TODIR)
run_rsync('-a', f'--stop-at={future}', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'--stop-at future {rel}')

# --- --stop-at in the past: rejected at parse -------------------------------
rmtree(TODIR)
proc = run_rsync('-a', '--stop-at=2000-01-01T00:00', f'{src}/', f'{TODIR}/',
                 check=False)
if proc.returncode == 0:
    test_fail("--stop-at with a past time was not rejected")

# --- --stop-after (minutes): parses, transfer completes ---------------------
rmtree(TODIR)
run_rsync('-a', '--stop-after=60', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'--stop-after {rel}')

print("stop-time: --stop-at future/past and --stop-after verified")
