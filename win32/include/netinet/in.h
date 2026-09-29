/*
 * <netinet/in.h> for the Windows build.
 *
 * The sockaddr_in/in6 types, htons() and friends come from winsock2.h and
 * ws2tcpip.h, which win32compat.h includes in the required order.  See
 * win32/include/unistd.h for why these shims exist.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32_SHIM_NETINET_IN_H
#define RSYNC_WIN32_SHIM_NETINET_IN_H

#include "win32/win32compat.h"

#endif
