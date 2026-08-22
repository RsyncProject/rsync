/*
 * LTFS (Linear Tape File System) awareness for rsync.
 *
 * Copyright (C) 2026 Wayne Davison & contributors
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 *
 * ---------------------------------------------------------------------------
 *
 * On an LTFS-mounted tape every file's metadata -- name, size, mtime, and the
 * physical block where its data begins -- lives in the tape index, which is
 * served from memory once the cartridge is mounted.  Reading that metadata is
 * therefore cheap, so building rsync's file list from an LTFS source is fast.
 *
 * Reading file *content*, on the other hand, requires physically positioning
 * the tape.  rsync's normal name-sorted traversal bears no relation to the
 * order in which bytes are laid down on the medium, so a restore that opens
 * files in name order makes the drive seek back and forth ("shoe-shining"),
 * turning a single forward streaming pass into hours of repositioning.
 *
 * This module lets the generator drive the data-read phase in physical
 * (start-block) order instead.  LTFS exposes each file's starting block as a
 * virtual extended attribute; we read it during generation and hand the
 * generator a permutation of the file list sorted by that block, so the tape
 * streams forward in one pass.  Entries whose start block is unknown
 * (directories, symlinks, files not on tape) sort first, in their original
 * order, which conveniently front-loads directory creation before the bulk
 * data read begins.
 */

#include "rsync.h"
#ifdef SUPPORT_XATTRS
#include "lib/sysxattrs.h"
#endif

extern int ltfs_mode;
extern int startblock_ndx;

/* Return the LTFS starting block of fname, or -1 if it cannot be determined.
 * The attribute value is an ASCII decimal block number. */
int64 ltfs_startblock(const char *fname)
{
#ifdef SUPPORT_XATTRS
	/* The virtual xattr names under which LTFS publishes a file's starting
	 * data block.  The bare "ltfs.*" name is what an LTFS FUSE mount
	 * presents; the "user.ltfs.*" alias lets the feature be exercised on an
	 * ordinary filesystem (e.g. by the test suite) where only the "user."
	 * namespace is writable. */
	static const char *startblock_attrs[] = {
		"ltfs.startblock",
		"user.ltfs.startblock",
	};
	char buf[32];
	unsigned int i;

	for (i = 0; i < sizeof startblock_attrs / sizeof startblock_attrs[0]; i++) {
		ssize_t len = sys_lgetxattr(fname, startblock_attrs[i], buf, sizeof buf - 1);
		if (len > 0) {
			char *end;
			int64 blk;
			buf[len] = '\0';
			blk = (int64)strtoll(buf, &end, 10);
			if (end != buf && blk >= 0)
				return blk;
		}
	}
#else
	(void)fname;
#endif
	return -1;
}

struct ltfs_ent {
	int64 startblock;
	int idx;		/* index into flist->sorted[] */
};

static int ltfs_ent_cmp(const void *a, const void *b)
{
	const struct ltfs_ent *ea = a, *eb = b;

	if (ea->startblock != eb->startblock)
		return ea->startblock < eb->startblock ? -1 : 1;
	/* Stable tie-break so unknown-block entries (all -1, e.g. directories)
	 * keep their original parent-before-child name ordering. */
	return ea->idx < eb->idx ? -1 : ea->idx > eb->idx ? 1 : 0;
}

/* Build a tape-physical read order for the active range of flist.  Returns a
 * malloc'd array of (flist->high - flist->low + 1) entries, each an index into
 * flist->sorted[], ordered by ascending LTFS start block.  The caller iterates
 * the returned array in place of the natural low..high sweep.  Returns NULL
 * (caller falls back to natural order) if ltfs_mode is off, no start-block
 * metadata was negotiated, or the range is empty. */
int *ltfs_build_order(struct file_list *flist)
{
	struct ltfs_ent *ents;
	int *order;
	int n, j, count;

	if (!ltfs_mode || !startblock_ndx || flist->high < flist->low)
		return NULL;

	n = flist->high - flist->low + 1;
	ents = new_array(struct ltfs_ent, n);
	order = new_array(int, n);

	for (j = 0, count = 0; j < n; j++) {
		struct file_struct *file = flist->sorted[flist->low + j];
		ents[count].idx = flist->low + j;
		if (F_IS_ACTIVE(file) && S_ISREG(file->mode))
			ents[count].startblock = F_STARTBLOCK(file);
		else
			ents[count].startblock = -1;
		count++;
	}

	qsort(ents, count, sizeof ents[0], ltfs_ent_cmp);

	for (j = 0; j < count; j++)
		order[j] = ents[j].idx;

	free(ents);
	return order;
}
