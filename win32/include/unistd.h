/*
 * <unistd.h> for the Windows build.
 *
 * MSVC has no <unistd.h>, but rsync and popt include it directly.  Everything
 * they use from it -- read/write/close, access, getcwd, getpid, the uid
 * calls -- is supplied by win32compat.h, which config.h has already pulled
 * in.  This shim exists purely so the include resolves, which is what keeps
 * the upstream sources free of #ifdef _WIN32.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32_SHIM_UNISTD_H
#define RSYNC_WIN32_SHIM_UNISTD_H

#include "win32/win32compat.h"

#endif
