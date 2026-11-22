#!/usr/bin/env python3

import locale
import os
import subprocess

from harness import metadata
from harness.daemon import start_test_daemon
from harness.daemon_config import write_daemon_conf
from harness.environment import FROMDIR, SCRATCHDIR, TODIR
from harness.results import test_fail
from harness.rsync import rsync_argv

metadata(features={'daemon'}, transports={'pipe'})

FROMDIR.mkdir()
(FROMDIR / 'locale-marker').write_bytes(b'contents\n')
saved = locale.setlocale(locale.LC_CTYPE)
locales = ['C']
try:
    for names in (('tr_TR.UTF-8', 'tr_TR.utf8'), ('tr_TR.ISO-8859-9', 'tr_TR.iso88599')):
        for lang in names:
            try:
                locale.setlocale(locale.LC_CTYPE, lang)
            except locale.Error:
                continue
            locales.append(lang)
            break
finally:
    locale.setlocale(locale.LC_CTYPE, saved)

for lang in locales:
    logfile = SCRATCHDIR / f'log-{lang}'
    include = SCRATCHDIR / f'include-{lang}.conf'
    include.write_text(
        f'TRANSFER LOGGING = yes\nLOG FORMAT = entry:%n\n'
        f'[module]\npath = {FROMDIR}\nread only = yes\n'
    )
    conf = write_daemon_conf([], global_options={'log file': str(logfile), '&INCLUDE': str(include)},
                             name=f'rsync-{lang}.conf')
    url = start_test_daemon(conf, 12894)
    dest = TODIR / lang
    dest.mkdir(parents=True)
    for token in ('flist', 'FLIST'):
        proc = subprocess.run(rsync_argv('-r', f'--info={token}', f'{url}module/', f'{dest}/'),
                              env={**os.environ, 'LC_ALL': lang},
                              capture_output=True, timeout=30)
        if proc.returncode != 0:
            test_fail(f'{lang} --info={token}: exit {proc.returncode}: {proc.stderr!r}')
    if b'] entry:locale-marker\n' not in logfile.read_bytes():
        test_fail(f'{lang}: uppercase transfer logging directive was not applied')
    if (dest / 'locale-marker').read_bytes() != b'contents\n':
        test_fail(f'{lang}: file not transferred intact')
    print(f'{lang}: ASCII options and configuration keywords accepted')
if len(locales) < 3:
    print('some Turkish locales unavailable')
