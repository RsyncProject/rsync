#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import SCRATCHDIR, makepath, rmtree, rsync_argv, test_fail

SECRET = 'TOP-SECRET-PASSWORD-abc123'

base = SCRATCHDIR / 'filter-merge-content-echo'
rmtree(base)
src = base / 'src'
dest = base / 'dest'
makepath(src, dest)
(src / 'f').write_bytes(b'hi\n')

secret = base / 'secret'
secret.write_text(SECRET + '\n')

def invoke(*args):
    result = subprocess.run(rsync_argv(*args), capture_output=True, text=True, timeout=30)
    return result, result.stdout + result.stderr

(src / '.rsync-filter').write_text(': ../secret\n')
result, out = invoke('-r', '-F', f'{src}/', f'{dest}/')
if result.returncode == 0:
    test_fail('invalid merge rule was accepted')
if SECRET in out:
    test_fail(f'merge-file content leaked: {out!r}')
if 'a file read earlier' not in out:
    test_fail(f'merge-file error lacks source context: {out!r}')

(base / 'rules').write_text(SECRET + '\n')
result, out = invoke('-r', f'--filter=. {base}/rules', f'{src}/', f'{dest}/')
if result.returncode == 0 or SECRET in out:
    test_fail(f'argument merge file leaked or was accepted: {out!r}')
if 'rules line 1' not in out:
    test_fail(f'argument merge-file location is missing: {out!r}')

(base / 'outer-known').write_text(f'. {base}/inner-bad\n')
(base / 'inner-bad').write_text(SECRET + '\n')
result, out = invoke('-r', f'--filter=. {base}/outer-known', f'{src}/', f'{dest}/')
if SECRET in out or 'inner-bad' in out:
    test_fail(f'indirect merge-file details leaked: {out!r}')
if 'a file named at' not in out or 'outer-known line 1' not in out:
    test_fail(f'indirect merge-file source is missing: {out!r}')

result, out = invoke('-r', '--filter=Zbogus-rule-text', f'{src}/', f'{dest}/')
if result.returncode == 0 or 'Zbogus-rule-text' not in out:
    test_fail(f'argument rule diagnostic changed: {out!r}')

(src / '.rsync-filter').write_text('- *.tmp\n')
(src / 'keep.txt').write_bytes(b'keep\n')
(src / 'drop.tmp').write_bytes(b'drop\n')
result, _ = invoke('-r', '-F', f'{src}/', f'{dest}/')
if result.returncode:
    test_fail(f'valid merge file failed: {result.stderr!r}')
if not (dest / 'keep.txt').exists() or (dest / 'drop.tmp').exists():
    test_fail('valid merge rule was not applied')

(base / 'outer').write_text(f'. {base}/nested\n')
(base / 'nested').write_text(f'. {SECRET}\n')
result, out = invoke('-r', f'--filter=. {base}/outer', f'{src}/', f'{dest}/')
if result.returncode == 0 or SECRET in out:
    test_fail(f'nested merge-file content leaked or was accepted: {out!r}')

(base / 'child').write_text('- harmless\n')
(base / 'parent').write_text(f'. {base}/child\nZbad-in-parent\n')
result, out = invoke('-r', f'--filter=. {base}/parent', f'{src}/', f'{dest}/')
if 'parent line 2' not in out:
    test_fail(f'nested merge-file line is wrong: {out!r}')

(base / 'crlf').write_bytes(b'- harmless\r\nZbad-on-line-2\r\n')
result, out = invoke('-r', f'--filter=. {base}/crlf', f'{src}/', f'{dest}/')
if 'crlf line 2' not in out:
    test_fail(f'CRLF miscounted the line: out={out!r}')

(src / '.rsync-filter').write_text(':w- ../secret\n')
result, out = invoke('-r', '-F', '--debug=FILTER2', f'{src}/', f'{dest}/')
if SECRET in out:
    test_fail(f'filter debug trace leaked merge-file content: {out!r}')

PATTERN_TEXT = 'sec[r]et-marker-9x'
MATCHED_NAME = 'secret-marker-9x'
rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'keep\n')
(src / MATCHED_NAME).write_bytes(b'hidden\n')
(src / '.rsync-filter').write_text('- ' + PATTERN_TEXT + '\n')
result, out = invoke('-r', '-F', '-vv', f'{src}/', f'{dest}/')
if result.returncode:
    test_fail(f'the -vv transfer failed: out={out!r}')
if 'because of' not in out:
    test_fail(f'no match was reported, so this case exercises nothing: out={out!r}')
if PATTERN_TEXT in out:
    test_fail(f'match trace leaked a file-derived pattern: {out!r}')
if (dest / MATCHED_NAME).exists():
    test_fail('file-derived rule did not hide the matching file')

rmtree(src)
makepath(src / 'sub')
(src / 'sub' / 'f').write_bytes(b'x\n')
maxpath = os.pathconf(str(base), 'PC_PATH_MAX')
overflowed = False
for slack in range(4, 80, 4):
    pad = maxpath - len(str(src)) - slack
    if pad < 32:
        continue
    (src / '.rsync-filter').write_text(': sub/DEFERRED-SECRET-' + 'Q' * pad + '\n')
    result, out = invoke('-r', '-F', f'{src}/', f'{dest}/')
    if 'DEFERRED-SECRET' in out:
        test_fail(f'overlong merge name leaked: {out!r}')
    if 'merge-file name overflows' in out:
        overflowed = True
        break
if not overflowed:
    test_fail('merge-name overflow path was not reached')

DEFERRED_NAME = 'named-from-f[i]le-x9'
(src / '.rsync-filter').write_text(': ' + DEFERRED_NAME + '\n')
result, out = invoke('-r', '-F', '-vvv', f'{src}/', f'{dest}/')
if DEFERRED_NAME in out:
    test_fail(f'deferred merge name leaked: {out!r}')

rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'x\n')
(src / '.rsync-filter').write_text('- trailing-ws-probe \n')
result, out = invoke('-r', '-F', '--debug=FILTER1', f'{src}/', f'{dest}/')
if 'trailing-ws-probe' in out or 'CAUTION' in out:
    test_fail(f'file-derived trailing whitespace leaked: {out!r}')

result, out = invoke('-r', '--debug=FILTER1', '--filter=- my-own-rule ',
               f'{src}/', f'{dest}/')
if 'CAUTION' not in out or 'my-own-rule' not in out:
    test_fail(f'argument trailing-whitespace warning is missing: {out!r}')

long_arg = 'ARGLONG-' + 'Q' * (maxpath + 200)
result, out = invoke('-r', f'--filter=- {long_arg}', f'{src}/', f'{dest}/')
if 'discarding over-long filter' not in out:
    test_fail(f'expected the over-long path: out={out!r}')
if out.count('Q') <= maxpath:
    test_fail('overlong argument rule was truncated')

SELF_PATTERN = 'excl-self-[x]9'
SELF_MATCHED = 'excl-self-x9'
rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'keep\n')
(src / SELF_MATCHED).write_bytes(b'hidden\n')
(src / '.rsync-filter').write_text(':e ' + SELF_PATTERN + '\n')
result, out = invoke('-r', '-F', '-vv', f'{src}/', f'{dest}/')
if result.returncode:
    test_fail(f'the :e transfer failed: out={out!r}')
if 'because of' not in out:
    test_fail(f'no match was reported, so this case exercises nothing: out={out!r}')
if SELF_PATTERN in out:
    test_fail(f'exclude-self trace leaked a merge-file pattern: {out!r}')

rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'x\n')
(src / '.rsync-filter').write_text('Zbad-perdir\n')
result, out = invoke('-r', '-F', f'{src}/', f'{dest}/')
if result.returncode == 0 or 'Zbad-perdir' in out:
    test_fail(f'per-directory rule leaked or was accepted: {out!r}')
if '.rsync-filter line 1' not in out:
    test_fail(f'per-directory merge-file location is missing: {out!r}')
