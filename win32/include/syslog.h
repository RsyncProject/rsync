/*
 * <syslog.h> for the Windows build.
 *
 * Windows has no syslog; win32compat.h defines the LOG_* levels and makes
 * openlog()/syslog()/closelog() discard, so the daemon logging paths still
 * compile.  rsync's own --log-file handling is unaffected.  See
 * win32/include/unistd.h for why these shims exist.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32_SHIM_SYSLOG_H
#define RSYNC_WIN32_SHIM_SYSLOG_H

#include "win32/win32compat.h"

#endif
