#!/usr/bin/env python3
import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail, test_skipped

base = SCRATCHDIR / 'output-control-chars'
src = base / 'src'
dst = base / 'dst'
rmtree(base)
src.mkdir(parents=True)
dst.mkdir()

src_b = os.fsencode(src)
names = {
    'raw_csi': b'raw_\x9b_name',
    'utf8_csi': b'utf8_\xc2\x9b_name',
    'valid_utf8': b'valid_\xd8\x9b_name',
    'leading_cr': b'\rleading_cr_name',
    'delete': b'delete_\x7f_name',
}
created = {}
for label, name in names.items():
    try:
        with open(src_b + b'/' + name, 'wb') as fh:
            fh.write(b'x')
        created[label] = name
    except OSError:
        pass

if 'utf8_csi' not in created:
    test_skipped("filesystem rejects a UTF-8-encoded C1 filename")

proc = subprocess.run(
    rsync_argv('-av', '--8-bit-output', str(src) + '/', str(dst) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if proc.returncode != 0:
    test_fail(f"rsync failed with status {proc.returncode}: {proc.stderr!r}")

output = proc.stdout + proc.stderr
if b'\xc2\x9b' in output:
    test_fail("UTF-8-encoded CSI reached terminal output")
if b'\\#302\\#233' not in output:
    test_fail("UTF-8-encoded CSI was not escaped byte-for-byte")
if 'raw_csi' in created and names['raw_csi'] in output:
    test_fail("raw CSI reached terminal output")
if 'raw_csi' in created and b'\\#233' not in output:
    test_fail("raw CSI was not escaped")
if 'valid_utf8' in created and names['valid_utf8'] not in output:
    test_fail("valid UTF-8 containing a C1-range continuation byte was changed")
if 'leading_cr' in created and names['leading_cr'] in output:
    test_fail("leading carriage return reached terminal output")
if 'leading_cr' in created and b'\\#015leading_cr_name' not in output:
    test_fail("leading carriage return was not escaped")
if 'delete' in created and names['delete'] in output:
    test_fail("DEL reached terminal output")
if 'delete' in created and b'delete_\\#177_name' not in output:
    test_fail("DEL was not escaped")

print("output-control-chars: terminal controls escaped and valid UTF-8 preserved")
