/*
 * Report the symlinks and hard links met during a run.
 *
 * Neither is a first-class concept for ordinary Windows users: creating a
 * symlink needs Developer Mode or SeCreateSymbolicLinkPrivilege, which most
 * accounts do not hold, and hard links are rare outside developer tooling.
 * This build therefore leaves SUPPORT_LINKS and SUPPORT_HARD_LINKS off, so
 * rsync treats each one as the ordinary file it resolves to:
 *
 *   - a symlink is followed, and its referent's contents are copied;
 *   - hard links to one inode are copied as that many independent files.
 *
 * That is the sensible default here, but it silently loses structure the
 * user may have meant to keep.  The stat layer (win32/win32compat.c) notices
 * both cases as it walks a tree, records them here, and this prints a
 * summary as the process exits -- so the outcome is stated plainly rather
 * than discovered later.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

#define MAX_NOTED 100

struct noted {
	char *path;
	int   kind;
};

static struct noted noted[MAX_NOTED];
static int noted_cnt;
static int noted_of_kind[2];
static int seen_cnt[2];
static int printer_registered;
static CRITICAL_SECTION lock;
static int lock_ready;

void win32_links_init(void)
{
	InitializeCriticalSection(&lock);
	lock_ready = 1;
}

static void print_summary(void)
{
	int kind, i;

	if (!noted_cnt)
		return;

	fprintf(stderr, "\n");
	for (kind = 0; kind < 2; kind++) {
		if (!seen_cnt[kind])
			continue;
		if (kind == WIN32_LINK_SYMLINK) {
			if (seen_cnt[kind] == 1)
				fprintf(stderr, "rsync: 1 symlink was followed "
						"and copied as an ordinary file:\n");
			else
				fprintf(stderr, "rsync: %d symlinks were followed "
						"and copied as ordinary files:\n",
					seen_cnt[kind]);
		} else {
			if (seen_cnt[kind] == 1)
				fprintf(stderr, "rsync: 1 hard-linked file was "
						"copied as an independent file:\n");
			else
				fprintf(stderr, "rsync: %d hard-linked files were "
						"copied as independent files:\n",
					seen_cnt[kind]);
		}
		for (i = 0; i < noted_cnt; i++) {
			if (noted[i].kind == kind)
				fprintf(stderr, "    %s\n", noted[i].path);
		}

		if (seen_cnt[kind] > noted_of_kind[kind])
			fprintf(stderr, "    ... and %d more not listed\n",
				seen_cnt[kind] - noted_of_kind[kind]);
	}
	fprintf(stderr,
		"This build does not reproduce links: Windows needs a privilege "
		"most\naccounts lack to create a symlink, so neither is treated "
		"as first-class.\n");
	fflush(stderr);
}

void win32_note_link(const char *path, int kind)
{
	int i;

	if (kind != WIN32_LINK_SYMLINK && kind != WIN32_LINK_HARDLINK)
		return;
	if (!lock_ready)
		return;

	EnterCriticalSection(&lock);

	for (i = 0; i < noted_cnt; i++) {
		if (noted[i].kind == kind && strcmp(noted[i].path, path) == 0) {
			LeaveCriticalSection(&lock);
			return;
		}
	}

	seen_cnt[kind]++;

	if (noted_cnt < MAX_NOTED) {
		char *copy = strdup(path);
		if (copy) {
			noted[noted_cnt].path = copy;
			noted[noted_cnt].kind = kind;
			noted_cnt++;
			noted_of_kind[kind]++;
		}
	}

	if (!printer_registered) {

		atexit(print_summary);
		printer_registered = 1;
	}

	LeaveCriticalSection(&lock);
}

int win32_symlink(const char *target, const char *linkpath)
{
	(void)target;
	(void)linkpath;
	errno = ENOSYS;
	return -1;
}
