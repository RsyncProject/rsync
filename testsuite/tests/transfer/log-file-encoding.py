#!/usr/bin/env python3

import codecs
import errno
import locale
import os
import subprocess
import sys

from harness import metadata
from harness.environment import SCRATCHDIR, TOOLDIR
from harness.results import test_fail
from harness.rsync import rsync_argv

metadata(transports={'pipe'})

UNSUPPORTED_NAMES = {errno.EILSEQ, errno.EINVAL, errno.ENOTSUP}
C_NAMES = {
    b'plain-file': b'plain-file',
    b'sharp-\xc3\x9f\xc3\x9f': b'sharp-\\#303\\#237\\#303\\#237',
    b'turkish-I-\xc4\xb1': b'turkish-I-\\#304\\#261',
    b'legacy-\xe9': b'legacy-\\#351',
    b'line-\rline-\t\n': b'line-\\#015line-\t\\#012',
}
UTF8_NAMES = {
    **C_NAMES,
    b'sharp-\xc3\x9f\xc3\x9f': b'sharp-\xc3\x9f\xc3\x9f',
    b'turkish-I-\xc4\xb1': b'turkish-I-\xc4\xb1',
    b'valid-\xe2\x82\xac\xf0\x90\x90\x80': b'valid-\xe2\x82\xac\xf0\x90\x90\x80',
    b'control-\xc2\x85': b'control-\\#302\\#205',
    b'short-\xe2\x82': b'short-\\#342\\#202',
    b'overlong-\xe0\x80\x80': b'overlong-\\#340\\#200\\#200',
    b'surrogate-\xed\xa0\x80': b'surrogate-\\#355\\#240\\#200',
    b'out-of-range-\xf4\x90\x80\x80': b'out-of-range-\\#364\\#220\\#200\\#200',
}


def available_locale(names, encoding):
    if not hasattr(locale, 'nl_langinfo') or not hasattr(locale, 'CODESET'):
        return None
    expected = codecs.lookup(encoding).name
    saved = locale.setlocale(locale.LC_CTYPE)
    try:
        for name in names:
            try:
                locale.setlocale(locale.LC_CTYPE, name)
                actual = codecs.lookup(locale.nl_langinfo(locale.CODESET)).name
            except (locale.Error, LookupError):
                continue
            if actual == expected:
                return name
    finally:
        locale.setlocale(locale.LC_CTYPE, saved)
    return None


def ascii_fallback(label):
    config = TOOLDIR / 'config.h'
    if not config.is_file():
        return False
    features = {line.split()[1] for line in config.read_text().splitlines()
                if line.startswith('#define ')}
    locale_support = {'CONFIG_LOCALE', 'HAVE_LOCALE_H', 'HAVE_SETLOCALE'}
    codeset_query = ({'HAVE_LANGINFO_H', 'HAVE_NL_LANGINFO'} <= features
                     or {'HAVE_LIBCHARSET_H', 'HAVE_LOCALE_CHARSET'} <= features)
    return (not locale_support <= features or not codeset_query
            or (label not in ('utf8', 'turkish-utf8')
                and not {'HAVE_WCHAR_H', 'HAVE_MBRTOWC'} <= features))


def escape_nonascii(data):
    return b''.join(bytes([byte]) if byte < 128 else f'\\#{byte:03o}'.encode('ascii')
                    for byte in data)


def check_log_encoding(label, active_locale, names, *options):
    base = SCRATCHDIR / label
    source = base / 'source'
    dest = base / 'dest'
    logfile = base / 'rsync.log'
    source.mkdir(parents=True)
    dest.mkdir()

    source_bytes = os.fsencode(source)
    created = set()
    for name in names:
        try:
            with open(os.path.join(source_bytes, name), 'wb') as entry:
                entry.write(b'data\n')
        except OSError as error:
            if error.errno not in UNSUPPORTED_NAMES:
                raise
            print(f'{label}: filesystem refuses {name!r}')
        else:
            created.add(name)

    # A rewritten directory entry may not be usable for lookup or removal.
    listed = set(os.listdir(source_bytes))
    for name in sorted(created - listed):
        os.unlink(os.path.join(source_bytes, name))
        print(f'{label}: filesystem did not preserve {name!r}')
    created.intersection_update(listed)
    if not created:
        return
    expected = sorted(b'\\#134#123|' + names[name] for name in created)
    fallback = ascii_fallback(label)
    if fallback:
        expected = sorted(escape_nonascii(name) for name in expected)

    # Exercise a literal escape without using a backslash in a filename.
    proc = subprocess.run(
        rsync_argv('-r', *options, f'--log-file={logfile}',
                   r'--log-file-format=entry:\#123|%f', '--', *sorted(created), f'{dest}/'),
        cwd=source, capture_output=True, timeout=30,
    )
    if proc.returncode != 0:
        test_fail(f'{label}: transfer failed ({proc.returncode}): {proc.stderr!r}')
    if set(os.listdir(os.fsencode(dest))) != created:
        test_fail(f'{label}: logging changed the transferred filenames')

    data = logfile.read_bytes()
    entries = [line.partition(b'] entry:')[2] for line in data.split(b'\n')
               if b'] entry:' in line]
    if sorted(entries) != expected:
        test_fail(f'{label} ({active_locale}): expected {expected!r}, got {sorted(entries)!r}')
    if fallback or label in ('c', 'c-8bit', 'posix', 'utf8', 'turkish-utf8'):
        try:
            data.decode('ascii' if fallback or label in ('c', 'c-8bit', 'posix') else 'utf-8')
        except UnicodeDecodeError as error:
            test_fail(f'{label}: log is not valid text: {error}')
    print(f'{label}: {active_locale}, {len(entries)} logfile entries')


# Cygwin decodes this ISO-8859-3 byte where glibc rejects it.
LATIN3_D0 = b'\xd0' if sys.platform == 'cygwin' else b'\\#320'

CASES = {
    'c': (None, ('C',), C_NAMES, ()),
    'c-8bit': (None, ('C',), C_NAMES, ('--8-bit-output',)),
    'posix': (None, ('POSIX',), C_NAMES, ()),
    'utf8': ('UTF-8', ('C.UTF-8', 'C.utf8', 'en_US.UTF-8', 'en_US.utf8', 'UTF-8'), UTF8_NAMES, ()),
    'turkish-utf8': ('UTF-8', ('tr_TR.UTF-8', 'tr_TR.utf8'), UTF8_NAMES, ()),
    'turkish-legacy': ('ISO-8859-9', ('tr_TR.ISO-8859-9', 'tr_TR.iso88599'),
                       {b'turkish-I-\xfd': b'turkish-I-\xfd'}, ()),
    'latin1': ('ISO-8859-1', ('en_US.ISO-8859-1', 'en_US.iso88591'),
               {b'legacy-\xc3\x85': b'legacy-\xc3\\#205'}, ()),
    'latin3': ('ISO-8859-3', ('mt_MT.ISO-8859-3', 'mt_MT.iso88593'),
               {b'legacy-\xd0\xb1': b'legacy-' + LATIN3_D0 + b'\xb1'}, ()),
    'euc-jp': ('EUC-JP', ('ja_JP.eucJP', 'ja_JP.ujis', 'japanese.euc'),
               {b'legacy-\x8e\xb1': b'legacy-\x8e\xb1'}, ()),
}


def main():
    if len(sys.argv) == 2:
        label = sys.argv[1]
        _, _, names, options = CASES[label]
        check_log_encoding(label, os.environ['LC_ALL'], names, *options)
        return

    for label, (encoding, candidates, _, _) in CASES.items():
        active_locale = available_locale(candidates, encoding) if encoding else candidates[0]
        if active_locale is None:
            print(f'{label}: no active {encoding} locale', flush=True)
            continue
        # Cygwin chooses its filename encoding when the process starts.
        env = {**os.environ, 'LC_ALL': active_locale}
        env.pop('RSYNC_ICONV', None)
        proc = subprocess.run(
            [sys.executable, '-B', '-S', '-m', 'harness.execute', __file__, label],
            env=env, timeout=35,
        )
        if proc.returncode:
            raise SystemExit(proc.returncode)


if __name__ == '__main__':
    main()
