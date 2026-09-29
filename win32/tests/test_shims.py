#!/usr/bin/env python3
"""Every shim macro in win32compat.h must be undone in win32undef.h.

win32compat.h is pulled in by config.h, so it is visible tree-wide: a macro
there redirects a POSIX name to the win32_* function that implements it.
The files under win32/ are where those implementations live, and each one
includes win32undef.h so that it can reach the real CRT or Winsock call.

If a macro is missing from win32undef.h the implementation calls itself.
That is not hypothetical -- win32_signal() did exactly that before
"#undef signal" was added, and the symptom was rsync pinning a core at
startup with no output, which took a debugger to explain.  The invariant is
maintained by hand, so check it here rather than trusting it.

Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
"""

import re

from wintest import SRCDIR, check, ok

compat = (SRCDIR / 'win32' / 'win32compat.h').read_text(encoding='utf-8')
undef = (SRCDIR / 'win32' / 'win32undef.h').read_text(encoding='utf-8')

# A shim is a function-like macro whose replacement starts with win32_ or an
# underscore (the CRT spelling, e.g. #define mkdir(p, m) _mkdir(p)).
shims = {}
for m in re.finditer(
        r'^#define\s+([a-z_][a-z0-9_]*)\s*\([^)]*\)\s+(win32_|_)', compat,
        re.M | re.I):
    shims[m.group(1)] = m.start()

undone = set(re.findall(r'^#undef\s+([A-Za-z_][A-Za-z0-9_]*)', undef, re.M))

check(shims, 'found no shim macros at all -- has win32compat.h moved?')

# Names whose implementation is spelled differently from the macro, so the
# macro cannot make them recurse.  Each needs a reason, not just an entry.
EXEMPT = {
    # implemented as win32_ftruncate64(), and "ftruncate64" is undone
    'ftruncate',
    # the port's own hooks; they expand to win32_init() and
    # win32_fix_path_args(), which nothing under win32/ calls by the hook name
    'platform_init',
    'platform_fix_path_args',
}

missing = sorted(set(shims) - undone - EXEMPT)
check(not missing,
      'these win32compat.h macros have no #undef in win32undef.h, so the\n'
      'win32/*.c function implementing each one would call itself:\n'
      + '\n'.join(f'    {name}' for name in missing))

# Guard the exemption list too: if one of these ever gains an #undef, or the
# macro goes away, the list should shrink rather than quietly rot.
stale = sorted(n for n in EXEMPT if n not in shims or n in undone)
check(not stale,
      'EXEMPT in this test lists names that are no longer shim macros '
      f'without an #undef: {stale}. Remove them.')

ok(f'{len(shims)} shim macros checked, {len(shims) - len(EXEMPT)} undone in '
   'win32undef.h and 3 exempt by name')
