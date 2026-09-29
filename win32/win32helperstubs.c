/*
 * Definitions the tls and trimslash helpers need on Windows, and nowhere
 * else.
 *
 * Makefile.in links those two against syscall.c but not util1.c.  Windows has
 * no AT_FDCWD, so syscall.c compiles the portable secure_relative_open()
 * fallback, which calls pathjoin() -- and pathjoin lives in util1.c.  Linking
 * util1.c in turn drags in the log-level arrays that rsync's real logging
 * would define.
 *
 * The other helpers (t_unsafe, t_chmod_secure, t_secure_relpath) already
 * declare these themselves, which is why they don't link this file: doing so
 * would define them twice.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"

short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];
