/*
 * opendir/readdir/closedir on top of FindFirstFile/FindNextFile.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

struct DIR {
	HANDLE           handle;
	WIN32_FIND_DATAA find;
	int              pending;
	struct dirent    ent;
};

DIR *win32_opendir(const char *path)
{
	DIR *dirp;
	char pattern[MAXPATHLEN];
	size_t len = strlen(path);

	if (len == 0 || len + 3 >= sizeof pattern) {
		errno = len ? ENAMETOOLONG : ENOENT;
		return NULL;
	}

	memcpy(pattern, path, len);
	if (pattern[len - 1] != '/' && pattern[len - 1] != '\\')
		pattern[len++] = '\\';
	pattern[len++] = '*';
	pattern[len] = '\0';

	dirp = (DIR *)calloc(1, sizeof *dirp);
	if (!dirp) {
		errno = ENOMEM;
		return NULL;
	}

	dirp->handle = FindFirstFileA(pattern, &dirp->find);
	if (dirp->handle == INVALID_HANDLE_VALUE) {
		DWORD err = GetLastError();
		free(dirp);
		errno = win32_oserr_to_errno(err);
		return NULL;
	}
	dirp->pending = 1;
	return dirp;
}

struct dirent *win32_readdir(DIR *dirp)
{
	if (!dirp) {
		errno = EBADF;
		return NULL;
	}

	if (!dirp->pending) {
		if (!FindNextFileA(dirp->handle, &dirp->find)) {

			if (GetLastError() != ERROR_NO_MORE_FILES)
				errno = EIO;
			return NULL;
		}
	}
	dirp->pending = 0;

	{
		size_t n = strlen(dirp->find.cFileName);

		if (n >= sizeof dirp->ent.d_name)
			n = sizeof dirp->ent.d_name - 1;
		memcpy(dirp->ent.d_name, dirp->find.cFileName, n);
		dirp->ent.d_name[n] = '\0';
	}
	dirp->ent.d_ino = 0;

	if (win32_attrs_are_symlink(dirp->find.dwFileAttributes,
				    dirp->find.dwReserved0))
		dirp->ent.d_type = DT_LNK;
	else if (dirp->find.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
		dirp->ent.d_type = DT_DIR;
	else
		dirp->ent.d_type = DT_REG;

	return &dirp->ent;
}

int win32_closedir(DIR *dirp)
{
	if (!dirp) {
		errno = EBADF;
		return -1;
	}
	if (dirp->handle != INVALID_HANDLE_VALUE)
		FindClose(dirp->handle);
	free(dirp);
	return 0;
}
