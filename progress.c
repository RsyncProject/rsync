/*
 * Routines to output progress information during a file transfer.
 *
 * Copyright (C) 1996-2000 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2001, 2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2003-2022 Wayne Davison
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
 */

#include "rsync.h"
#include "inums.h"

extern int am_server;
extern int flist_eof;
extern int quiet;
extern int human_readable;
extern int need_unsorted_flist;
extern int output_needs_newline;
extern int stdout_format_has_i;
extern struct stats stats;
extern int inc_recurse;
extern struct file_list *cur_flist;

BOOL want_progress_now = False;

#define PROGRESS_HISTORY_SECS 5

#ifdef GETPGRP_VOID
#define GETPGRP_ARG
#else
#define GETPGRP_ARG 0
#endif

struct progress_history {
	struct timeval time;
	OFF_T ofs;
};

static struct progress_history ph_start;
static struct progress_history ph_list[PROGRESS_HISTORY_SECS];
static int newest_hpos, oldest_hpos;
static int current_file_index;
static OFF_T unchecked_size; /* size of the entries not yet checked for a transfer */

static unsigned long msdiff(struct timeval *t1, struct timeval *t2)
{
	return (t2->tv_sec - t1->tv_sec) * 1000L
	     + (t2->tv_usec - t1->tv_usec) / 1000;
}


/**
 * @param ofs Current position in file
 * @param size Total size of file
 * @param is_last True if this is the last time progress will be
 * printed for this file, so we should output a newline.  (Not
 * necessarily the same as all bytes being received.)
 * @param transfer_done True only for the final whole-transfer summary.
 **/
static void rprint_progress(OFF_T ofs, OFF_T size, struct timeval *now,
			    int is_last, int transfer_done)
{
	char rembuf[64], eol[128];
	const char *rate_str;
	unsigned long diff;
	double rate, remain;
	int pct, rate_human_level;
	BOOL overall = INFO_GTE(PROGRESS, 2);

	if (is_last) {
		int len = snprintf(eol, sizeof eol,
			" (xfr#%d, %s-chk=%d/%d)\n",
			stats.xferred_files, transfer_done || flist_eof ? "to" : "ir",
			transfer_done ? 0 : stats.num_files - current_file_index - 1,
			stats.num_files);
		if (overall) {
			static int last_len = 0;
			/* Drop \n and pad with spaces if line got shorter. */
			if (last_len < --len)
				last_len = len;
			eol[last_len] = '\0';
			while (last_len > len)
				eol[--last_len] = ' ';
		}
		/* Compute stats based on the starting info. */
		if (!ph_start.time.tv_sec || !(diff = msdiff(&ph_start.time, now)))
			diff = 1;
		rate = (double) (ofs - ph_start.ofs) * 1000.0 / diff;
	} else {
		strlcpy(eol, "  ", sizeof eol);
		/* Compute stats based on recent progress. */
		if (!(diff = msdiff(&ph_list[oldest_hpos].time, now)))
			diff = 1;
		rate = (double) (ofs - ph_list[oldest_hpos].ofs) * 1000.0 / diff;
	}

	if (transfer_done || (is_last && !overall))
		remain = (double) diff / 1000.0;
	else
		remain = rate ? (double) (size - ofs) / rate : 0.0;

	if (remain < 0 || remain > 9999.0 * 3600.0)
		strlcpy(rembuf, "  ??:??:??", sizeof rembuf);
	else {
		snprintf(rembuf, sizeof rembuf, "%4u:%02u:%02u",
			 (unsigned int) (remain / 3600.0),
			 (unsigned int) (remain / 60.0) % 60,
			 (unsigned int) remain % 60);
	}

	output_needs_newline = 0;
	/* The total can be zero while data is in transit: a file that was
	 * empty when the file list was built may have grown since. */
	pct = ofs == size ? 100 : size ? (int) (100.0 * ofs / size) : 0;
	/* Keep progress rates scaled while byte counts retain the level-1 default. */
	rate_human_level = human_readable == 1 ? 2 : human_readable;
	rate_str = rate >= (double)INT64_MAX ? ">MAX "
		: do_big_num((int64)rate, rate_human_level, NULL);
	rput_progress();
	rprintf(FCLIENT, "%15sB %3d%% %7sB/s %s%s", human_num(ofs), pct,
		rate_str, rembuf, eol);
	if ((!is_last || overall) && !quiet) {
		output_needs_newline = 1;
		rflush(FCLIENT);
	}
}

void progress_init(void)
{
	if (!am_server && !INFO_GTE(PROGRESS, 1)) {
		struct timeval now;
		gettimeofday(&now, NULL);
		ph_start.time.tv_sec = now.tv_sec;
		ph_start.time.tv_usec = now.tv_usec;
	}
}

/* The size that an entry has in the transfer, as counted by stats.total_size. */
static OFF_T entry_size(struct file_struct *fp)
{
	if (!F_IS_ACTIVE(fp) || !(S_ISREG(fp->mode) || S_ISLNK(fp->mode)))
		return 0;
	return F_LENGTH(fp);
}

/* Without incremental recursion the generator walks the one file list in
 * sorted order, so once an entry is being transferred every earlier entry has
 * been checked, and at the end of the list all of them have.  Given the
 * number of sorted entries that have been checked, keep the size of the ones
 * that are still ahead of the transfer. */
static void note_checked_files(int checked)
{
	static int next_unchecked = -1;
	int j;

	if (inc_recurse)
		return;

	if (next_unchecked < 0) {
		for (j = 0; j < cur_flist->used; j++)
			unchecked_size += entry_size(cur_flist->sorted[j]);
		next_unchecked = 0;
	}

	while (next_unchecked < checked)
		unchecked_size -= entry_size(cur_flist->sorted[next_unchecked++]);
}

/* The total that progress2 measures the transferred size against.  When the
 * whole file list is known up front this is the size transferred so far plus
 * the size of the entries that have not been checked yet, which leaves out
 * the files that needed no transfer and counts a file that is sent again as
 * often as it is sent. */
static OFF_T overall_size(void)
{
	if (inc_recurse)
		return stats.total_size;
	return stats.total_transferred_size + unchecked_size;
}

void set_current_file_index(struct file_struct *file, int ndx)
{
	if (!file)
		current_file_index = cur_flist->used + cur_flist->ndx_start - 1;
	else if (need_unsorted_flist)
		current_file_index = flist_find(cur_flist, file) + cur_flist->ndx_start;
	else
		current_file_index = ndx;
	/* Every sorted entry up to and including this one has been checked. */
	note_checked_files(current_file_index - cur_flist->ndx_start + 1);
	current_file_index -= cur_flist->flist_num;
}

void instant_progress(const char *fname)
{
	/* We only get here if want_progress_now is True */
	if (!stdout_format_has_i && !INFO_GTE(NAME, 1))
		rprintf(FINFO, "%s\n", fname);
	end_progress(0);
	want_progress_now = False;
}

void end_progress(OFF_T size)
{
	if (!am_server) {
		struct timeval now;
		gettimeofday(&now, NULL);
		if (INFO_GTE(PROGRESS, 2) || want_progress_now) {
			rprint_progress(stats.total_transferred_size,
					overall_size(), &now, True, False);
		} else {
			rprint_progress(size, size, &now, True, False);
			memset(&ph_start, 0, sizeof ph_start);
		}
	}
}

void finish_progress(void)
{
	struct timeval now;

	if (am_server || !INFO_GTE(PROGRESS, 2))
		return;

	gettimeofday(&now, NULL);
	rprint_progress(stats.total_transferred_size, overall_size(), &now, True, True);
}

void show_progress(OFF_T ofs, OFF_T size)
{
	struct timeval now;
#if defined HAVE_GETPGRP && defined HAVE_TCGETPGRP
	static pid_t pgrp = -1;
	pid_t tc_pgrp;
#endif

	if (am_server)
		return;

#if defined HAVE_GETPGRP && defined HAVE_TCGETPGRP
	if (pgrp == -1)
		pgrp = getpgrp(GETPGRP_ARG);
#endif

	gettimeofday(&now, NULL);

	if (INFO_GTE(PROGRESS, 2)) {
		ofs = stats.total_transferred_size - size + ofs;
		size = overall_size();
	}

	if (!ph_start.time.tv_sec) {
		int i;

		/* Try to guess the real starting time when the sender started
		 * to send us data by using the time we last received some data
		 * in the last file (if it was recent enough). */
		if (msdiff(&ph_list[newest_hpos].time, &now) <= 1500) {
			ph_start.time = ph_list[newest_hpos].time;
			ph_start.ofs = 0;
		} else {
			ph_start.time.tv_sec = now.tv_sec;
			ph_start.time.tv_usec = now.tv_usec;
			ph_start.ofs = ofs;
		}

		for (i = 0; i < PROGRESS_HISTORY_SECS; i++)
			ph_list[i] = ph_start;
	}
	else {
		if (msdiff(&ph_list[newest_hpos].time, &now) < 1000)
			return;

		newest_hpos = oldest_hpos;
		oldest_hpos = (oldest_hpos + 1) % PROGRESS_HISTORY_SECS;
		ph_list[newest_hpos].time.tv_sec = now.tv_sec;
		ph_list[newest_hpos].time.tv_usec = now.tv_usec;
		ph_list[newest_hpos].ofs = ofs;
	}

#if defined HAVE_GETPGRP && defined HAVE_TCGETPGRP
	tc_pgrp = tcgetpgrp(STDOUT_FILENO);
	if (tc_pgrp != pgrp && tc_pgrp != -1)
		return;
#endif

	rprint_progress(ofs, size, &now, False, False);
}
