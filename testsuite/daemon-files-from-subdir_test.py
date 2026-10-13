#!/usr/bin/env python3
# Functional regression: a --files-from pull whose source is a SUBDIRECTORY of
# a daemon module (use chroot = no) sends nothing -- every listed file is
# reported as "file has vanished" and the transfer ends in code 24, while the
# same files pulled from the module root transfer fine.
#
# Root cause: the sender opens each file through secure_relative_open() from
# the module root, rebuilding the path as F_PATHNAME + "/" + f_name.  A
# --files-from entry has no F_PATHNAME: it is relative to orig_dir, the
# files-from source directory, which here is a module subdirectory.  Dropping
# orig_dir makes the sender look for module/<entry> instead of
# module/subdir/<entry>, so the open fails with ENOENT.  --remove-source-files
# rebuilt the parent directory of each sent file the same way, so it must
# remove the files under the subdirectory and leave same-named files at the
# module root alone.  Runs at any uid.

from rsyncfns import (
    SCRATCHDIR, assert_same, makepath, rmtree, run_rsync, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12912
ENTRIES = ('top.txt', 'deep/nested.txt')

served = SCRATCHDIR / 'served'
golden = SCRATCHDIR / 'golden'
listing = SCRATCHDIR / 'files-from.list'
for root in (served, golden):
    rmtree(root)
    for tree, label in (('subdir', 'subdir'), ('.', 'module-root')):
        makepath(root / tree / 'deep')
        for rel in ENTRIES:
            (root / tree / rel).write_text(f"{label} {rel}\n")
listing.write_text('\n'.join(ENTRIES) + '\n')

conf = write_daemon_conf([
    ('mod', {'path': str(served), 'read only': 'no', 'use chroot': 'no'}),
])
url = start_test_daemon(conf, DAEMON_PORT)


def pull(dst, *opts):
    rmtree(dst)
    makepath(dst)
    proc = run_rsync('-rt', *opts, f'--files-from={listing}', f'{url}mod/subdir/',
                     f'{dst}/', check=False, capture_output=True)
    print(proc.stdout, proc.stderr)
    if proc.returncode != 0:
        test_fail(f"--files-from {' '.join(opts)} pull from a module subdirectory "
                  f"failed (rc={proc.returncode}):\n{proc.stderr}")
    for rel in ENTRIES:
        if not (dst / rel).is_file():
            test_fail(f"--files-from {' '.join(opts)} pull did not deliver {rel}")
        assert_same(golden / 'subdir' / rel, dst / rel, label=rel)


pull(SCRATCHDIR / 'pulldst')

pull(SCRATCHDIR / 'removedst', '--remove-source-files')
for rel in ENTRIES:
    if (served / 'subdir' / rel).exists():
        test_fail(f"--remove-source-files left subdir/{rel} in the module")
    assert_same(golden / rel, served / rel, label=f"module-root {rel}")
