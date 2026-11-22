#!/usr/bin/env python3

import locale
import os
import subprocess

from harness import metadata
from harness.environment import FROMDIR, TODIR
from harness.results import test_fail
from harness.rsync import rsh_cmd, rsync_argv, rsync_path_arg

metadata(transports={'pipe'})

FROMDIR.mkdir()
TODIR.mkdir()
for size in (0, 1, 511, 512, 513, 1024, 1280, 1536, 1537):
    with (FROMDIR / str(size)).open('wb') as file:
        file.truncate(size)


def selected(args, lang):
    proc = subprocess.run(rsync_argv('-rni', '--out-format=item:%n', *args),
                          env={**os.environ, 'LC_ALL': lang},
                          capture_output=True, timeout=30)
    if proc.returncode != 0:
        test_fail(f'{lang} {args}: exit {proc.returncode}: {proc.stderr!r}')
    return sorted(int(line[5:]) for line in proc.stdout.splitlines()
                  if line.startswith(b'item:') and line[5:].isdigit())


def check_size(value, expected, lang):
    entries = selected((f'--max-size={value}', f'{FROMDIR}/', f'{TODIR}/'), lang)
    if entries != expected:
        test_fail(f'{lang} --max-size={value}: expected {expected}, got {entries}')


check_size('.5K', [0, 1, 511, 512], 'C')
check_size('1.5KiB-1', [0, 1, 511, 512, 513, 1024, 1280], 'C')
check_size('1.499999999999999999999999999999K',
           [0, 1, 511, 512, 513, 1024, 1280], 'C')
check_size('2147483648-1', [0, 1, 511, 512, 513, 1024, 1280, 1536, 1537], 'C')
proc = subprocess.run(rsync_argv('--block-size=0.000001G', '--version'),
                      env={**os.environ, 'LC_ALL': 'C'}, capture_output=True, timeout=30)
if proc.returncode != 0:
    test_fail(f'a small block size in GiB was refused: {proc.stderr!r}')
for invalid in ('', 'K', '.K', '1.5KK', '1.5K+2', '0-1'):
    proc = subprocess.run(rsync_argv(f'--max-size={invalid}', '--version'),
                          env={**os.environ, 'LC_ALL': 'C'},
                          capture_output=True, timeout=30)
    if proc.returncode != 1:
        test_fail(f'--max-size={invalid!r}: expected syntax error, got {proc.returncode}')

saved = locale.setlocale(locale.LC_NUMERIC)
comma_locale = None
try:
    for lang in ('tr_TR.UTF-8', 'tr_TR.utf8', 'de_DE.UTF-8', 'de_DE.utf8'):
        try:
            locale.setlocale(locale.LC_NUMERIC, lang)
        except locale.Error:
            continue
        if locale.localeconv()['decimal_point'] == ',':
            comma_locale = lang
            break
finally:
    locale.setlocale(locale.LC_NUMERIC, saved)

if comma_locale:
    expected = [0, 1, 511, 512, 513, 1024, 1280, 1536]
    check_size('1.5KIB', expected, comma_locale)
    check_size('1,5KiB', expected, comma_locale)
    entries = selected(
        ('--max-size=1,5KiB', '--max-alloc=1,5M', f'--rsh={rsh_cmd()}',
         f'--rsync-path=env LC_ALL=C {rsync_path_arg()}', f'{FROMDIR}/', f'localhost:{TODIR}/'),
        comma_locale,
    )
    if entries != expected:
        test_fail(f'mixed-locale push: expected {expected}, got {entries}')
    print(f'{comma_locale}: decimal period and comma produce the same byte limit')
else:
    print('comma-decimal locale unavailable')
