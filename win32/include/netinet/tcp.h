/*
 * <netinet/tcp.h> for the Windows build.
 *
 * TCP_NODELAY is defined by winsock2.h, which win32compat.h includes.  See
 * win32/include/unistd.h for why these shims exist.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32_SHIM_NETINET_TCP_H
#define RSYNC_WIN32_SHIM_NETINET_TCP_H

#include "win32/win32compat.h"

#endif
