/*
   Copyright (C) Andrew Tridgell 1996
   Copyright (C) Paul Mackerras 1996

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

#include "rsync.h"

extern int verbose;
extern int am_server;
extern int do_progress;
extern int checksum_seed;
extern int inplace;
extern int make_backups;

typedef unsigned short tag;

#define TABLESIZE (1<<16)
#define NULL_TAG ((size_t)-1)

static int false_alarms;
static int tag_hits;
static int matches;
static int64 data_transfer;

static int total_false_alarms;
static int total_tag_hits;
static int total_matches;

extern struct stats stats;

struct target {
	tag t;
	size_t i;
};

static struct target *targets;

static size_t *tag_table;

#define gettag2(s1,s2) (((s1) + (s2)) & 0xFFFF)
#define gettag(sum) gettag2((sum)&0xFFFF,(sum)>>16)

static int compare_targets(struct target *t1,struct target *t2)
{
	return (int)t1->t - (int)t2->t;
}


static void build_hash_table(struct sum_struct *s)
{
	size_t i;

	if (!tag_table)
		tag_table = new_array(size_t, TABLESIZE);

	targets = new_array(struct target, s->count);
	if (!tag_table || !targets)
		out_of_memory("build_hash_table");

	for (i = 0; i < s->count; i++) {
		targets[i].i = i;
		targets[i].t = gettag(s->sums[i].sum1);
	}

	qsort(targets,s->count,sizeof(targets[0]),(int (*)())compare_targets);

	for (i = 0; i < TABLESIZE; i++)
		tag_table[i] = NULL_TAG;

	for (i = s->count; i-- > 0; )
		tag_table[targets[i].t] = i;
}


static OFF_T last_match;


/**
 * Transmit a literal and/or match token.
 *
 * This delightfully-named function is called either when we find a
 * match and need to transmit all the unmatched data leading up to it,
 * or when we get bored of accumulating literal data and just need to
 * transmit it.  As a result of this second case, it is called even if
 * we have not matched at all!
 *
 * @param i If >0, the number of a matched token.  If 0, indicates we
 * have only literal data.
 **/
static void matched(int f,struct sum_struct *s,struct map_struct *buf,
		    OFF_T offset,int i)
{
	OFF_T n = offset - last_match;
	OFF_T j;

	if (verbose > 2 && i >= 0)
		rprintf(FINFO,"match at %.0f last_match=%.0f j=%d len=%u n=%.0f\n",
			(double)offset,(double)last_match,i,s->sums[i].len,(double)n);

	send_token(f,i,buf,last_match,n,i<0?0:s->sums[i].len);
	data_transfer += n;

	if (i >= 0) {
		stats.matched_data += s->sums[i].len;
		n += s->sums[i].len;
	}

	for (j = 0; j < n; j += CHUNK_SIZE) {
		int n1 = MIN(CHUNK_SIZE,n-j);
		sum_update(map_ptr(buf,last_match+j,n1),n1);
	}


	if (i >= 0)
		last_match = offset + s->sums[i].len;
	else
		last_match = offset;

	if (buf && do_progress) {
		show_progress(last_match, buf->file_size);

		if (i == -1)
			end_progress(buf->file_size);
	}
}


static void hash_search(int f,struct sum_struct *s,
			struct map_struct *buf, OFF_T len)
{
	OFF_T offset, end, backup;
	unsigned int k;
	size_t want_i;
	char sum2[SUM_LENGTH];
	uint32 s1, s2, sum;
	int more;
	schar *map;

	/* want_i is used to encourage adjacent matches, allowing the RLL
	 * coding of the output to work more efficiently. */
	want_i = 0;

	if (verbose > 2) {
		rprintf(FINFO,"hash search b=%u len=%.0f\n",
			s->blength, (double)len);
	}

	k = MIN(len, s->blength);

	map = (schar *)map_ptr(buf, 0, k);

	sum = get_checksum1((char *)map, k);
	s1 = sum & 0xFFFF;
	s2 = sum >> 16;
	if (verbose > 3)
		rprintf(FINFO, "sum=%.8x k=%u\n", sum, k);

	offset = 0;

	end = len + 1 - s->sums[s->count-1].len;

	if (verbose > 3) {
		rprintf(FINFO, "hash search s->blength=%u len=%.0f count=%.0f\n",
			s->blength, (double)len, (double)s->count);
	}

	do {
		tag t = gettag2(s1,s2);
		int done_csum2 = 0;
		size_t j = tag_table[t];

		if (verbose > 4)
			rprintf(FINFO,"offset=%.0f sum=%08x\n",(double)offset,sum);

		if (j == NULL_TAG)
			goto null_tag;

		sum = (s1 & 0xffff) | (s2 << 16);
		tag_hits++;
		do {
			unsigned int l;
			size_t i = targets[j].i;

			if (sum != s->sums[i].sum1)
				continue;

			/* also make sure the two blocks are the same length */
			l = MIN((OFF_T)s->blength, len-offset);
			if (l != s->sums[i].len)
				continue;

			/* inplace: ensure chunk's offset is either >= our
			 * offset or that the data didn't move. */
			if (inplace && !make_backups && s->sums[i].offset < offset
			    && !(s->sums[i].flags & SUMFLG_SAME_OFFSET))
				continue;

			if (verbose > 3)
				rprintf(FINFO,"potential match at %.0f target=%.0f %.0f sum=%08x\n",
					(double)offset,(double)j,(double)i,sum);

			if (!done_csum2) {
				map = (schar *)map_ptr(buf,offset,l);
				get_checksum2((char *)map,l,sum2);
				done_csum2 = 1;
			}

			if (memcmp(sum2,s->sums[i].sum2,s->s2length) != 0) {
				false_alarms++;
				continue;
			}

			/* If inplace is enabled, the best possible match is
			 * one with an identical offset, so we prefer that over
			 * the following want_i optimization. */
			if (inplace && !make_backups) {
				do {
					size_t i2 = targets[j].i;
					if (s->sums[i2].offset != offset)
						continue;
					if (i2 != i) {
						if (sum != s->sums[i2].sum1)
							break;
						if (memcmp(sum2, s->sums[i2].sum2,
							   s->s2length) != 0)
							break;
						i = i2;
					}
					/* This chunk was at the same offset on
					 * both the sender and the receiver. */
					s->sums[i].flags |= SUMFLG_SAME_OFFSET;
					goto set_want_i;
				} while (++j < s->count && targets[j].t == t);
			}

			/* we've found a match, but now check to see
			 * if want_i can hint at a better match. */
			if (i != want_i && want_i < s->count
			    && (!inplace || make_backups || s->sums[want_i].offset >= offset
			     || s->sums[want_i].flags & SUMFLG_SAME_OFFSET)
			    && sum == s->sums[want_i].sum1
			    && memcmp(sum2, s->sums[want_i].sum2, s->s2length) == 0) {
				/* we've found an adjacent match - the RLL coder
				 * will be happy */
				i = want_i;
			}
		    set_want_i:
			want_i = i + 1;

			matched(f,s,buf,offset,i);
			offset += s->sums[i].len - 1;
			k = MIN(s->blength, len-offset);
			map = (schar *)map_ptr(buf, offset, k);
			sum = get_checksum1((char *)map, k);
			s1 = sum & 0xFFFF;
			s2 = sum >> 16;
			matches++;
			break;
		} while (++j < s->count && targets[j].t == t);

	null_tag:
		backup = offset - last_match;
		/* We sometimes read 1 byte prior to last_match... */
		if (backup < 0)
			backup = 0;

		/* Trim off the first byte from the checksum */
		more = offset + k < len;
		map = (schar *)map_ptr(buf, offset - backup, k + more + backup)
		    + backup;
		s1 -= map[0] + CHAR_OFFSET;
		s2 -= k * (map[0]+CHAR_OFFSET);

		/* Add on the next byte (if there is one) to the checksum */
		if (more) {
			s1 += map[k] + CHAR_OFFSET;
			s2 += s1;
		} else
			--k;

		/* By matching early we avoid re-reading the
		   data 3 times in the case where a token
		   match comes a long way after last
		   match. The 3 reads are caused by the
		   running match, the checksum update and the
		   literal send. */
		if (backup >= CHUNK_SIZE + s->blength
		    && end - offset > CHUNK_SIZE) {
			matched(f,s,buf,offset - s->blength, -2);
		}
	} while (++offset < end);

	matched(f,s,buf,len,-1);
	map_ptr(buf,len-1,1);
}


/**
 * Scan through a origin file, looking for sections that match
 * checksums from the generator, and transmit either literal or token
 * data.
 *
 * Also calculates the MD4 checksum of the whole file, using the md
 * accumulator.  This is transmitted with the file as protection
 * against corruption on the wire.
 *
 * @param s Checksums received from the generator.  If <tt>s->count ==
 * 0</tt>, then there are actually no checksums for this file.
 *
 * @param len Length of the file to send.
 **/
void match_sums(int f, struct sum_struct *s, struct map_struct *buf, OFF_T len)
{
	char file_sum[MD4_SUM_LENGTH];

	last_match = 0;
	false_alarms = 0;
	tag_hits = 0;
	matches = 0;
	data_transfer = 0;

	sum_init(checksum_seed);

	if (len > 0 && s->count>0) {
		build_hash_table(s);

		if (verbose > 2)
			rprintf(FINFO,"built hash table\n");

		hash_search(f,s,buf,len);

		if (verbose > 2)
			rprintf(FINFO,"done hash search\n");
	} else {
		OFF_T j;
		/* by doing this in pieces we avoid too many seeks */
		for (j = 0; j < len-CHUNK_SIZE; j += CHUNK_SIZE) {
			int n1 = MIN(CHUNK_SIZE,(len-CHUNK_SIZE)-j);
			matched(f,s,buf,j+n1,-2);
		}
		matched(f,s,buf,len,-1);
	}

	sum_end(file_sum);
	/* If we had a read error, send a bad checksum. */
	if (buf && buf->status != 0)
		file_sum[0]++;

	if (verbose > 2)
		rprintf(FINFO,"sending file_sum\n");
	write_buf(f,file_sum,MD4_SUM_LENGTH);

	if (targets) {
		free(targets);
		targets=NULL;
	}

	if (verbose > 2)
		rprintf(FINFO, "false_alarms=%d tag_hits=%d matches=%d\n",
			false_alarms, tag_hits, matches);

	total_tag_hits += tag_hits;
	total_false_alarms += false_alarms;
	total_matches += matches;
	stats.literal_data += data_transfer;
}

void match_report(void)
{
	if (verbose <= 1)
		return;

	rprintf(FINFO,
		"total: matches=%d  tag_hits=%d  false_alarms=%d data=%.0f\n",
		total_matches,total_tag_hits,
		total_false_alarms,
		(double)stats.literal_data);
}
