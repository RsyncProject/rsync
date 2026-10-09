"""Pin how `--files-from` treats a leading slash and ".." components.

The rsync(1) `--files-from` entry states the goal but not the mechanism: "any
leading slashes are removed and no '..' references are allowed to go higher than
the source dir."  "are removed" / "are not allowed" don't say what actually
happens to such an entry.  Measured behaviour:

  - a leading "/" is stripped and the entry is taken relative to the source dir;
  - a ".." component is CLAMPED (trimmed) so the path cannot rise above the
    source dir -- "../keep" resolves to "<source>/keep", NOT the parent's "keep".

So an entry that tries to escape silently resolves back inside the source; it
does not error out by virtue of the "..", and it never reaches a sibling of the
source.  Pure local client behaviour: no daemon/root/tcp.  Cross-version:
expected identical against --rsync-bin=old_versions/rsync_3.2.7.
"""

import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail

base = SCRATCHDIR / 'files-from-clamp'
rmtree(base)
base.mkdir(parents=True)

src = base / 'src'
src.mkdir()
(src / 'file').write_text('INSRC-FILE\n')
(src / 'keep').write_text('INSRC-KEEP\n')          # what "../keep" must clamp to
# A sibling of the source with the SAME basename: a real escape would grab this.
(base / 'keep').write_text('OUTSIDE-KEEP-MUST-NOT-APPEAR\n')

listf = base / 'list'
# leading-slash entry, a normal entry, and an escaping "../keep".
listf.write_text('/file\nkeep\n../keep\n')

dst = base / 'dst'
rmtree(dst)
dst.mkdir()
proc = subprocess.run(
    rsync_argv('-a', f'--files-from={listf}', f'{src}/', f'{dst}/'),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# leading "/" stripped -> taken relative to the source.
f = dst / 'file'
if not f.is_file() or f.read_text() != 'INSRC-FILE\n':
    test_fail("--files-from: leading-slash '/file' was not transferred relative "
              "to the source dir")

# "keep" and the clamped "../keep" both resolve to <source>/keep, never the
# parent's keep -> the escape was prevented.
k = dst / 'keep'
if not k.is_file():
    test_fail("--files-from: 'keep' did not transfer")
if k.read_text() != 'INSRC-KEEP\n':
    test_fail(f"--files-from: '../keep' escaped the source dir -- dst/keep is "
              f"{k.read_text()!r}, expected the in-source 'INSRC-KEEP'")

# No file should have landed outside dst (no sibling-of-dst named 'keep' created).
if (base / 'keep').read_text() != 'OUTSIDE-KEEP-MUST-NOT-APPEAR\n':
    test_fail("--files-from: the outside 'keep' was modified")

print("files-from-path-clamp: leading '/' stripped (entry taken relative to the "
      "source); '..' is clamped within the source dir so '../keep' resolves to "
      "<source>/keep and cannot reach a sibling of the source")
