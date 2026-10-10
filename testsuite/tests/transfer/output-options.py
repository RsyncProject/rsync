#!/usr/bin/env python3

import os
import platform
import re
import select
import subprocess

from harness.rsync import (
    FROMDIR, TODIR,
    assert_not_exists, make_data_file, make_tree, makepath, rmtree, rsync_argv,
    test_fail, verify_dirs,
)

src = FROMDIR

def out(*args, want_rc=0, env=None, text=True):
    p = subprocess.run(rsync_argv(*args), capture_output=True, text=text, env=env)
    if want_rc is not None and p.returncode != want_rc:
        err = p.stderr if text else p.stderr.decode('latin-1', 'replace')
        test_fail(f"rsync {' '.join(args)} exited {p.returncode}, "
                  f"expected {want_rc}:\n{err}")
    return p

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

p = out('-ai', f'{src}/', f'{TODIR}/')
if '>f+++++++++' not in p.stdout:
    test_fail(f"--itemize-changes missing create line:\n{p.stdout}")

rmtree(TODIR)
p = out('-ain', f'{src}/', f'{TODIR}/')
if '>f+++++++++' not in p.stdout:
    test_fail("--dry-run itemize output missing")
assert_not_exists(TODIR / 'f0', label='--dry-run created a file')

rmtree(TODIR)
p = out('-a', '--stats', f'{src}/', f'{TODIR}/')
if 'Number of files:' not in p.stdout or 'Total file size:' not in p.stdout:
    test_fail(f"--stats output missing expected lines:\n{p.stdout}")

if platform.system() == 'Linux':
    stats = out('-r', '--info=stats3', f'{src}/', f'{TODIR}/').stdout
    if stats.count('heap statistics') < 3:
        test_fail('--info=stats3 did not emit allocator statistics')

rmtree(TODIR)
p = out('-a', '--out-format=%n', f'{src}/', f'{TODIR}/')
if 'f0' not in p.stdout:
    test_fail(f"--out-format=%n did not emit filenames:\n{p.stdout}")

rmtree(TODIR)
(src / 'checksum-a').write_text('a\n')
(src / 'checksum-b').write_text('different\n')
checksum = out('-rc', '--out-format=%C %n', f'{src}/', f'{TODIR}/').stdout
digests = dict((match[2], match[1]) for match in re.finditer(r'^([0-9a-f]{16,128}) (\S+)$',
                                                             checksum, re.M))
if digests and (not {'checksum-a', 'checksum-b'} <= set(digests)
                or digests['checksum-a'] == digests['checksum-b']):
    test_fail('--out-format=%C did not distinguish file contents')
if not digests and not re.search(r'^ {8,} \S+$', checksum, re.M):
    test_fail('--out-format=%C emitted neither checksums nor the documented fallback')

rmtree(TODIR)
p = out('--list-only', '-r', f'{src}/', f'{TODIR}/')
if 'f0' not in p.stdout:
    test_fail(f"--list-only did not list files:\n{p.stdout}")
assert_not_exists(TODIR / 'f0', label='--list-only copied a file')

rmtree(TODIR)
p = out('-a', '-q', f'{src}/', f'{TODIR}/')
if p.stdout.strip() != '':
    test_fail(f"--quiet produced stdout: {p.stdout!r}")
verify_dirs(src, TODIR, label='--quiet still transferred')

rmtree(TODIR)
p = out('-a', '--progress', f'{src}/', f'{TODIR}/')
progress_re = r'[\d.,]+B\s+100%\s+[\d.,]+B/s\s+\d+:\d\d:\d\d'
if not re.search(progress_re, p.stdout):
    test_fail(f"--progress did not show the expected final shape:\n{p.stdout}")

rmtree(TODIR)
p = out('-a', '-q', '--progress', f'{src}/', f'{TODIR}/')
if p.stdout != '':
    test_fail(f"--quiet --progress produced stdout: {p.stdout!r}")
verify_dirs(src, TODIR, label='--quiet --progress still transferred')

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

rmtree(src)
rmtree(TODIR)
makepath(src)
weird = os.fsencode(str(src)) + b'/hi\xf9name'
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
    if rb'\#371' in noesc.stdout:
        rmtree(TODIR)
        makepath(TODIR)
        esc = out('-ai', '-8', f'{src}/', f'{TODIR}/', env=cenv, text=False)
        if rb'\#371' in esc.stdout:
            test_fail("-8 should leave the high-bit name byte unescaped, but "
                      f"the \\#371 escape was still present:\n{esc.stdout!r}")

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
if filter_msg not in p.stdout or filter_msg in p.stderr:
    test_fail('--debug=filter1 did not use the default stdout route')

rmtree(TODIR)
makepath(TODIR)
p = out('-r', '--exclude=*.tmp', '--debug=filter1', '--stderr=all',
        f'{src}/', f'{TODIR}/')
if filter_msg not in p.stderr or filter_msg in p.stdout:
    test_fail('--stderr=all did not route debug output exclusively to stderr')

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
