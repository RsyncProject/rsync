/*
 * <dirent.h> for the Windows build.
 *
 * struct dirent and the opendir()/readdir()/closedir() family are defined by
 * win32compat.h and implemented over FindFirstFile in win32/win32dir.c; see
 * win32/include/unistd.h for why these shims exist.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32_SHIM_DIRENT_H
#define RSYNC_WIN32_SHIM_DIRENT_H

#include "win32/win32compat.h"

#endif
