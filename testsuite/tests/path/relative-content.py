#!/usr/bin/env python3

from harness.rsync import SCRATCHDIR, makepath, rmtree, run_rsync, test_fail

base = SCRATCHDIR / 'relcontent'
src = base / 'src' / 'sub'
dest = base / 'dest'
rmtree(base)
makepath(src, dest)

payload = 'hello relative content\n'
(src / 'file').write_text(payload)

abs_file = str((src / 'file').resolve())
run_rsync('-aR', abs_file, str(dest) + '/')

landed = dest / abs_file.lstrip('/')
if not landed.is_file():
    test_fail(f"rsync -aR did not create {landed} (only the tree, no file?)")
got = landed.read_text()
if got != payload:
    test_fail(f"rsync -aR transferred empty/wrong content: {got!r} != {payload!r}")

print("relative-content: rsync -aR transfers file content for an absolute source")
