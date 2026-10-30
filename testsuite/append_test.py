#!/usr/bin/env python3
"""Coverage of --append and --append-verify at depth.

--append assumes each destination file is a prefix of the (longer) source and
transfers only the bytes past the existing size; it does NOT re-check the data
already present, and it never touches a file that is already the same size or
larger. --append-verify works the same way but folds the existing data into the
whole-file checksum, so a transfer whose result fails verification is re-sent
with a normal --inplace pass. Exercise both on files >=3 levels deep.
"""

import os

from rsyncfns import (
    FROMDIR, TODIR,
    assert_same, forced_protocol, make_tree, rmtree, run_rsync, test_fail,
    walk_files,
)

src = FROMDIR
deep = os.path.join('d1', 'd2', 'd3', 'f3')


def seed_source():
    rmtree(src)
    make_tree(src, depth=3, data=True, data_size=8192)
    return [p.relative_to(src) for p in walk_files(src)]


def dest_prefix(rels, *, corrupt=False, frac=0.5):
    """Build a destination holding the first `frac` of each source file (a
    valid prefix), optionally corrupting the deep file's leading bytes."""
    rmtree(TODIR)
    for rel in rels:
        dst = TODIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        full = (src / rel).read_bytes()
        dst.write_bytes(full[: int(len(full) * frac)])
    if corrupt:
        p = TODIR / deep
        bad = bytearray(p.read_bytes())
        bad[0:64] = b'\x00' * 64
        p.write_bytes(bytes(bad))


# --- --append completes truncated destinations at every level ---------------
rels = seed_source()
dest_prefix(rels)
run_rsync('-a', '--append', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'append {rel}')

# The split between non-verifying --append and verifying --append-verify only
# exists at protocol >= 30; at protocol 29 plain --append still verifies, so
# skip the distinguishing sub-cases there.
proto = forced_protocol()
if proto is not None and proto < 30:
    print(f"append: protocol {proto} -- skipping the --append/--append-verify "
          "split (verifying-append behaviour predates the protocol-30 split)")
else:
    # plain --append trusts a corrupted prefix (leaves it wrong)
    dest_prefix(rels, corrupt=True)
    run_rsync('-a', '--append', f'{src}/', f'{TODIR}/')
    if (TODIR / deep).read_bytes() == (src / deep).read_bytes():
        test_fail("plain --append unexpectedly repaired a corrupted prefix "
                  "(it should append only and trust the existing data)")

    # --append-verify detects the bad prefix and re-sends the whole file
    dest_prefix(rels, corrupt=True)
    run_rsync('-a', '--append-verify', f'{src}/', f'{TODIR}/')
    assert_same(TODIR / deep, src / deep, label='append-verify deep')

print("append: tail-only completion at depth; append-verify repairs prefix")
