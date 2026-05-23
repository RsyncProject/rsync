#!/usr/bin/env python3
"""Breadth coverage of the output / reporting options.

These options control rsync's OUTPUT, not its path handling, so they are
checked for the documented output shape rather than at depth:
  --version, --help, --itemize-changes (-i), --dry-run (-n), --stats,
  --out-format, --list-only, --quiet (-q), --progress, -h, -8.
"""

import subprocess

from rsyncfns import (
    FROMDIR, TODIR,
    assert_not_exists, make_tree, rmtree, rsync_argv, test_fail,
)

src = FROMDIR


def out(*args):
    return subprocess.run(rsync_argv(*args), capture_output=True, text=True)


# --- --version / --help -----------------------------------------------------
p = out('--version')
if p.returncode != 0 or 'protocol version' not in p.stdout:
    test_fail(f"--version output unexpected:\n{p.stdout}")
p = out('--help')
help_txt = p.stdout + p.stderr
if 'rsync' not in help_txt or 'Usage' not in help_txt:
    test_fail("--help did not print usage")

rmtree(src)
rmtree(TODIR)
make_tree(src, depth=2)

# --- --itemize-changes: a new file shows the create itemization -------------
p = out('-ai', f'{src}/', f'{TODIR}/')
if '>f+++++++++' not in p.stdout:
    test_fail(f"--itemize-changes missing create line:\n{p.stdout}")

# --- --dry-run lists but does not create ------------------------------------
rmtree(TODIR)
p = out('-ain', f'{src}/', f'{TODIR}/')
if '>f+++++++++' not in p.stdout:
    test_fail("--dry-run itemize output missing")
assert_not_exists(TODIR / 'f0', label='--dry-run created a file')

# --- --stats prints the summary block ---------------------------------------
rmtree(TODIR)
p = out('-a', '--stats', f'{src}/', f'{TODIR}/')
if 'Number of files:' not in p.stdout or 'Total file size:' not in p.stdout:
    test_fail(f"--stats output missing expected lines:\n{p.stdout}")

# --- --out-format=%n emits bare filenames -----------------------------------
rmtree(TODIR)
p = out('-a', '--out-format=%n', f'{src}/', f'{TODIR}/')
if 'f0' not in p.stdout:
    test_fail(f"--out-format=%n did not emit filenames:\n{p.stdout}")

# --- --list-only lists the source without copying ---------------------------
# Pass a destination too: without --list-only this transfer would populate
# TODIR, so the assert_not_exists below actually proves the "without copying"
# property rather than being vacuously true for a destination-less command.
rmtree(TODIR)
p = out('--list-only', '-r', f'{src}/', f'{TODIR}/')
if 'f0' not in p.stdout:
    test_fail(f"--list-only did not list files:\n{p.stdout}")
assert_not_exists(TODIR / 'f0', label='--list-only copied a file')

# --- --quiet suppresses normal stdout ---------------------------------------
rmtree(TODIR)
p = out('-a', '-q', f'{src}/', f'{TODIR}/')
if p.stdout.strip() != '':
    test_fail(f"--quiet produced stdout: {p.stdout!r}")

# --- --progress shows a percentage ------------------------------------------
rmtree(TODIR)
p = out('-a', '--progress', f'{src}/', f'{TODIR}/')
if '100%' not in p.stdout:
    test_fail(f"--progress did not show a percentage:\n{p.stdout}")

# --- -h / -8 do not break a transfer ----------------------------------------
rmtree(TODIR)
p = out('-a', '-h', '-8', '--stats', f'{src}/', f'{TODIR}/')
if p.returncode != 0:
    test_fail(f"-h/-8 broke the transfer:\n{p.stderr}")

print("output-options: version/help/-i/-n/--stats/--out-format/--list-only/"
      "-q/--progress/-h/-8 verified")
