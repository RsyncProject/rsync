/*
 * The platform_fix_path_args() hook: translate path separators in the
 * command line.
 *
 * This lives apart from win32compat.c because it calls check_for_hostspec(),
 * which belongs to rsync's option parsing.  The test helpers (tls, trimslash,
 * ...) link the compat layer but not options.c, and a one-function file keeps
 * that dependency out of their way.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

void win32_fix_path_args(int argc, char *argv[])
{
	int i;

	for (i = 0; i < argc; i++) {
		char *host = NULL;
		int port = 0;

		if (!check_for_hostspec(argv[i], &host, &port))
			win32_normalize_path(argv[i]);
		if (host)
			free(host);
	}
}
