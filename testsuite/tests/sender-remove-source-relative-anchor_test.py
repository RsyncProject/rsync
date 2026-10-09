#!/usr/bin/env python3
"""Absolute --relative source cleanup must use the source's absolute parent."""

import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail


base = SCRATCHDIR.resolve() / "sender-remove-source-relative-anchor"
source = base / "source/sub/file"
dest = base / "dest"
rmtree(base)
source.parent.mkdir(parents=True)
dest.mkdir()
source.write_text("absolute-relative-source\n", encoding="utf-8")

proc = subprocess.run(
    rsync_argv("-aR", "--remove-source-files", str(source), str(dest) + "/"),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
if proc.returncode != 0:
    test_fail(
        "absolute -R --remove-source-files copied the file but failed cleanup "
        f"(rc={proc.returncode}):\n{proc.stdout}"
    )
if source.exists():
    test_fail("absolute -R --remove-source-files left its source behind")

copies = [
    path for path in dest.rglob("file")
    if path.is_file() and path.read_text(encoding="utf-8")
        == "absolute-relative-source\n"
]
if len(copies) != 1:
    test_fail(f"destination did not contain exactly one transferred file: {copies}")

print("absolute --relative source was copied and removed through its real parent")
