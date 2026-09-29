/*
 * <sys/ioctl.h> for the Windows build.
 *
 * Deliberately empty of TIOCGWINSZ: popt guards its terminal-width probe
 * with "#if defined(TIOCGWINSZ)", so leaving it undefined compiles that code
 * out and popt falls back to its default width.  See win32/include/unistd.h
 * for why these shims exist.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#ifndef RSYNC_WIN32_SHIM_SYS_IOCTL_H
#define RSYNC_WIN32_SHIM_SYS_IOCTL_H

#endif
