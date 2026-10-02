#!/usr/bin/env python3
import os
import subprocess

from rsyncfns import SCRATCHDIR, rmtree, rsync_argv, test_fail


def available_locales():
    proc = subprocess.run(['locale', '-a'], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        return {}
    return {name.lower(): name for name in proc.stdout.decode('ascii', 'ignore').splitlines()}


LOCALES = available_locales()


def find_locale(locale_names):
    return next((LOCALES[candidate.lower()] for candidate in locale_names
                 if candidate.lower() in LOCALES), None)


def check_locale_case(label, locale_names, name, expected, rejected):
    locale_name = find_locale(locale_names)
    if locale_name is None:
        return False

    case = base / label
    case_src = case / 'src'
    case_dst = case / 'dst'
    case_src.mkdir(parents=True)
    case_dst.mkdir()
    env = os.environ.copy()
    env['LC_ALL'] = locale_name

    try:
        subprocess.run([b'touch', os.fsencode(case_src) + b'/' + name],
                       env=env, check=True)
        proc = subprocess.run(
            rsync_argv('-av', '--8-bit-output', str(case_src) + '/',
                       str(case_dst) + '/'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        if proc.returncode != 0:
            test_fail(f"rsync failed under {locale_name}: {proc.stderr!r}")
        output = proc.stdout + proc.stderr
        if expected not in output or rejected in output:
            test_fail(f"incorrect filtering under {locale_name}: {output!r}")
    finally:
        subprocess.run([b'rm', b'-rf', b'--', os.fsencode(case)], env=env,
                       check=True)
    return True

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

utf8_locale = find_locale(('C.UTF-8', 'C.utf8', 'en_US.UTF-8', 'en_US.utf8'))
env = os.environ.copy()
if utf8_locale is not None:
    env['LC_ALL'] = utf8_locale
proc = subprocess.run(
    rsync_argv('-av', '--8-bit-output', str(src) + '/', str(dst) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
if proc.returncode != 0:
    test_fail(f"rsync failed with status {proc.returncode}: {proc.stderr!r}")

output = proc.stdout + proc.stderr
if utf8_locale is not None and 'utf8_csi' in created:
    if b'\xc2\x9b' in output:
        test_fail("UTF-8-encoded CSI reached terminal output")
    if b'\\#302\\#233' not in output:
        test_fail("UTF-8-encoded CSI was not escaped byte-for-byte")
if 'raw_csi' in created and names['raw_csi'] in output:
    test_fail("raw CSI reached terminal output")
if 'raw_csi' in created and b'\\#233' not in output:
    test_fail("raw CSI was not escaped")
if (utf8_locale is not None and 'valid_utf8' in created
        and names['valid_utf8'] not in output):
    test_fail("valid UTF-8 containing a C1-range continuation byte was changed")
if 'leading_cr' in created and names['leading_cr'] in output:
    test_fail("leading carriage return reached terminal output")
if 'leading_cr' in created and b'\\#015leading_cr_name' not in output:
    test_fail("leading carriage return was not escaped")
if 'delete' in created and names['delete'] in output:
    test_fail("DEL reached terminal output")
if 'delete' in created and b'delete_\\#177_name' not in output:
    test_fail("DEL was not escaped")

locale_cases = 0
locale_cases += check_locale_case(
    'iso-8859-1', ('en_US.ISO-8859-1', 'no_NO.ISO-8859-1'),
    b'iso_\xd8\x9b_name', b'iso_\xd8\\#233_name', b'iso_\xd8\x9b_name')
locale_cases += check_locale_case(
    'euc-jp', ('ja_JP.eucJP', 'ja_JP.ujis', 'japanese.euc'),
    b'euc_\x8e\xb1_name', b'euc_\x8e\xb1_name', b'euc_\\#216\\#261_name')

print("output-control-chars: terminal controls escaped and valid multibyte text "
      f"preserved ({locale_cases} optional locale cases)")
