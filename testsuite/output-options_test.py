#!/usr/bin/env python3
"""Breadth coverage of the output / reporting options.

These options control rsync's OUTPUT, not its path handling, so they are
checked for the documented output shape rather than at depth:
  --version, --help, --itemize-changes (-i), --dry-run (-n), --stats,
  --out-format, --list-only, --quiet (-q), --progress, -h, -8, --info,
  --debug, --stderr, --outbuf.

Every rsync run that is expected to succeed has its exit status checked (a
silent failure must not pass as "no output"), and the format-changing options
(-h, -8) assert the documented difference rather than merely "didn't break".
"""

import os
import re
import select
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
progress_re = r'[\d.,]+B\s+100%\s+[\d.,]+B/s\s+\d+:\d\d:\d\d'
if not re.search(progress_re, p.stdout):
    test_fail(f"--progress did not show the expected final shape:\n{p.stdout}")

# --quiet must suppress the progress carriage return as well as its text.
rmtree(TODIR)
p = out('-a', '-q', '--progress', f'{src}/', f'{TODIR}/')
if p.stdout != '':
    test_fail(f"--quiet --progress produced stdout: {p.stdout!r}")
verify_dirs(src, TODIR, label='--quiet --progress still transferred')

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

rmtree(TODIR)
human_binary = out('-a', '-hh', '--stats', f'{src}/', f'{TODIR}/').stdout
if 'Total file size: 48.8Ki bytes' not in human_binary:
    test_fail(f"-hh did not use dynamic binary-unit precision:\n{human_binary}")

rmtree(src)
rmtree(TODIR)
makepath(src)
make_data_file(src / 'threshold', 1024)
threshold = out('-a', '-hh', '--stats', f'{src}/', f'{TODIR}/').stdout
if 'Total file size: 1.00Ki bytes' not in threshold:
    test_fail(f"-hh did not show an exact-threshold binary unit:\n{threshold}")

rmtree(src)
rmtree(TODIR)
makepath(src)
make_data_file(src / 'below-threshold', 1000)
below_threshold = out('-a', '-hh', '--stats', f'{src}/', f'{TODIR}/').stdout
if 'Total file size: 1,000 bytes' not in below_threshold:
    test_fail("-hh should preserve raw byte formatting below the binary-unit "
              f"threshold:\n{below_threshold}")

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

# --- --info and --debug select output without blanket verbosity ------------
rmtree(src)
rmtree(TODIR)
makepath(src, TODIR)
(src / 'keep').write_text('keep\n')
(src / 'hide.tmp').write_text('hide\n')

p = out('-r', '--info=name1', f'{src}/', f'{TODIR}/')
if 'keep' not in p.stdout or 'hide.tmp' not in p.stdout:
    test_fail(f"--info=name1 did not report transferred names:\n{p.stdout}")

rmtree(TODIR)
makepath(TODIR)
p = out('-r', '--exclude=*.tmp', '--debug=filter1',
        f'{src}/', f'{TODIR}/')
filter_msg = '[sender] hiding file hide.tmp because of pattern *.tmp'
if filter_msg not in p.stdout or p.stderr:
    test_fail('--debug=filter1 did not use the default stdout route')

rmtree(TODIR)
makepath(TODIR)
p = out('-r', '--exclude=*.tmp', '--debug=filter1', '--stderr=all',
        f'{src}/', f'{TODIR}/')
if filter_msg not in p.stderr or p.stdout:
    test_fail('--stderr=all did not route debug output exclusively to stderr')

# --- --outbuf=L makes each complete output line observable immediately -----
rmtree(src)
rmtree(TODIR)
makepath(src, TODIR)
(src / 'a-small').write_text('small\n')
make_data_file(src / 'z-large', 256 * 1024)
proc = subprocess.Popen(
    rsync_argv('-r', '--outbuf=L', '--out-format=%n', '--bwlimit=128',
               f'{src}/', f'{TODIR}/'),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
ready, _, _ = select.select([proc.stdout], [], [], 5)
if not ready:
    proc.kill()
    proc.communicate()
    test_fail('--outbuf=L did not flush a complete output line within 5 seconds')
first_line = proc.stdout.readline()
if not first_line.strip() or not first_line.endswith('\n'):
    proc.kill()
    proc.communicate()
    test_fail(f'--outbuf=L returned an incomplete line: {first_line!r}')
if proc.poll() is not None:
    stdout, stderr = proc.communicate()
    test_fail('--outbuf=L transfer finished before buffering could be observed: '
              f'{first_line + stdout!r} {stderr!r}')
try:
    stdout, stderr = proc.communicate(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.communicate()
    test_fail('--outbuf=L transfer did not finish within 10 seconds')
if proc.returncode != 0:
    test_fail(f'--outbuf=L transfer exited {proc.returncode}: {stderr}')
if not (TODIR / 'z-large').is_file():
    test_fail('--outbuf=L transfer did not finish copying the payload')

print("output-options: version/help/-i/-n/--stats/--out-format/--list-only/"
      "-q/--progress/-h/-8/--info/--debug/--stderr/--outbuf verified")
