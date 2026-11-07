#!/usr/bin/env python3

import os

from harness.rsync import (
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

rels = seed_source()
dest_prefix(rels)
run_rsync('-a', '--append', f'{src}/', f'{TODIR}/')
for rel in rels:
    assert_same(TODIR / rel, src / rel, label=f'append {rel}')

proto = forced_protocol()
if proto is not None and proto < 30:
    print(f"append: protocol {proto} -- skipping the --append/--append-verify "
          "split (verifying-append behaviour predates the protocol-30 split)")
else:
    dest_prefix(rels, corrupt=True)
    run_rsync('-a', '--append', f'{src}/', f'{TODIR}/')
    if (TODIR / deep).read_bytes() == (src / deep).read_bytes():
        test_fail("plain --append unexpectedly repaired a corrupted prefix "
                  "(it should append only and trust the existing data)")

    dest_prefix(rels, corrupt=True)
    run_rsync('-a', '--append-verify', f'{src}/', f'{TODIR}/')
    assert_same(TODIR / deep, src / deep, label='append-verify deep')

print("append: tail-only completion at depth; append-verify repairs prefix")
