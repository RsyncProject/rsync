#!/usr/bin/env python3
"""Breadth coverage of the output / reporting options.

These options control rsync's OUTPUT, not its path handling, so they are
checked for the documented output shape rather than at depth:
  --version, --help, --itemize-changes (-i), --dry-run (-n), --stats,
  --out-format, --list-only, --quiet (-q), --progress, -h, -8.

Every rsync run that is expected to succeed has its exit status checked (a
silent failure must not pass as "no output"), and the format-changing options
(-h, -8) assert the documented difference rather than merely "didn't break".
"""

import os
import re
import subprocess

from rsyncfns import (
    FROMDIR, TODIR,
    assert_not_exists, make_data_file, make_tree, makepath, rmtree, rsync_argv,
    test_fail, verify_dirs,
)

src = FROMDIR


def out(*args, want_rc=0, env=None, text=True):
    """Run rsync capturing output. Unless want_rc is None, fail the test if the
    exit status isn't want_rc -- so a broken transfer can't masquerade as the
    expected (often empty) output."""
    p = subprocess.run(rsync_argv(*args), capture_output=True, text=text, env=env)
    if want_rc is not None and p.returncode != want_rc:
        err = p.stderr if text else p.stderr.decode('latin-1', 'replace')
        test_fail(f"rsync {' '.join(args)} exited {p.returncode}, "
                  f"expected {want_rc}:\n{err}")
    return p


# --- --version / --help -----------------------------------------------------
p = out('--version')
if 'protocol version' not in p.stdout:
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

# --- --quiet suppresses normal stdout BUT still transfers -------------------
# Checking only for empty stdout would also pass if the transfer silently
# failed, so confirm the destination actually received the tree.
rmtree(TODIR)
p = out('-a', '-q', f'{src}/', f'{TODIR}/')
if p.stdout.strip() != '':
    test_fail(f"--quiet produced stdout: {p.stdout!r}")
verify_dirs(src, TODIR, label='--quiet still transferred')

# --- --progress shows a percentage ------------------------------------------
rmtree(TODIR)
p = out('-a', '--progress', f'{src}/', f'{TODIR}/')
if '100%' not in p.stdout:
    test_fail(f"--progress did not show a percentage:\n{p.stdout}")

# --- -h / --human-readable formats byte counts with a unit suffix -----------
# Without -h, --stats prints grouped digits ("50,000 bytes"); with -h it uses a
# K/M/G suffix ("50.00K"). Use a file big enough that the two forms differ.
rmtree(src)
rmtree(TODIR)
makepath(src)
make_data_file(src / 'big', 50_000)
plain = out('-a', '--stats', f'{src}/', f'{TODIR}/').stdout
rmtree(TODIR)
human = out('-a', '-h', '--stats', f'{src}/', f'{TODIR}/').stdout
suffix_re = r'Total file size: [\d.,]+[KMG]'
if not re.search(suffix_re, human):
    test_fail(f"-h did not use a human-readable unit suffix:\n{human}")
if re.search(suffix_re, plain):
    test_fail(f"--stats without -h unexpectedly used a unit suffix:\n{plain}")

# --- -8 / --8-bit-output leaves high-bit filename bytes unescaped ------------
# rsync escapes non-printable name bytes as \#NNN; -8 prints 8-bit bytes raw.
# This needs a filename containing a high-bit byte and a C locale (where such a
# byte is non-printable). Best-effort: skip silently where the filesystem can't
# store the raw byte (macOS/Cygwin may reject or normalise it).
rmtree(src)
rmtree(TODIR)
makepath(src)
weird = os.fsencode(str(src)) + b'/hi\xf9name'   # 0xf9 -> octal 371
try:
    with open(weird, 'wb') as f:
        f.write(b"x\n")
    weird_ok = True
except OSError:
    weird_ok = False

if weird_ok:
    cenv = dict(os.environ, LC_ALL='C')
    makepath(TODIR)
    noesc = out('-ai', f'{src}/', f'{TODIR}/', env=cenv, text=False)
    if rb'\#371' in noesc.stdout:        # FS preserved the raw byte as expected
        rmtree(TODIR)
        makepath(TODIR)
        esc = out('-ai', '-8', f'{src}/', f'{TODIR}/', env=cenv, text=False)
        if rb'\#371' in esc.stdout:
            test_fail("-8 should leave the high-bit name byte unescaped, but "
                      f"the \\#371 escape was still present:\n{esc.stdout!r}")

print("output-options: version/help/-i/-n/--stats/--out-format/--list-only/"
      "-q/--progress/-h/-8 verified")
