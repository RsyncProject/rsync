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
extern int need_unsorted_flist;
extern int output_needs_newline;
extern int stdout_format_has_i;
extern struct stats stats;
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
 **/
static void rprint_progress(OFF_T ofs, OFF_T size, struct timeval *now, int is_last)
{
	char rembuf[64], eol[128];
	unsigned long diff;
	int64 rate, remain;
	int pct;

	if (is_last) {
		int len = snprintf(eol, sizeof eol,
			" (xfr#%d, %s-chk=%d/%d)\n",
			stats.xferred_files, flist_eof ? "to" : "ir",
			stats.num_files - current_file_index - 1,
			stats.num_files);
		if (INFO_GTE(PROGRESS, 2)) {
			static int last_len = 0;
			/* Drop \n and pad with spaces if line got shorter. */
			if (last_len < --len)
				last_len = len;
			eol[last_len] = '\0';
			while (last_len > len)
				eol[--last_len] = ' ';
			is_last = 0;
		}
		/* Compute stats based on the starting info. */
		if (!ph_start.time.tv_sec || !(diff = msdiff(&ph_start.time, now)))
			diff = 1;
		rate = (ofs - ph_start.ofs) * 1000 / diff;
		/* Switch to total time taken for our last update. */
		remain = diff;
	} else {
		strlcpy(eol, "  ", sizeof eol);
		/* Compute stats based on recent progress. */
		if (!(diff = msdiff(&ph_list[oldest_hpos].time, now)))
			diff = 1;
		rate = (ofs - ph_list[oldest_hpos].ofs) * 1000 / diff;
		remain = rate ? (size - ofs) / rate : 0;
	}

	if (remain < 0 || remain > (int64) 9999999 * 3600)
		strlcpy(rembuf, "  ??:??:??", sizeof rembuf);
	else {
		snprintf(rembuf, sizeof rembuf, "%4u:%02u:%02u",
			 (unsigned int) (remain / 3600),
			 (unsigned int) (remain / 60) % 60,
			 (unsigned int) remain % 60);
	}

	output_needs_newline = 0;
	pct = ofs == size ? 100 : (int) (100.0 * ofs / size);
	rprintf(FCLIENT, "\r%15sB %3d%% %7sB/s %s%s",
		human_num(ofs), pct, human_num(rate), rembuf, eol);
	if (!is_last && !quiet) {
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

void set_current_file_index(struct file_struct *file, int ndx)
{
	if (!file)
		current_file_index = cur_flist->used + cur_flist->ndx_start - 1;
	else if (need_unsorted_flist)
		current_file_index = flist_find(cur_flist, file) + cur_flist->ndx_start;
	else
		current_file_index = ndx;
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
					stats.total_size, &now, True);
		} else {
			rprint_progress(size, size, &now, True);
			memset(&ph_start, 0, sizeof ph_start);
		}
	}
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
		size = stats.total_size;
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

	rprint_progress(ofs, size, &now, False);
}
