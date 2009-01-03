/*
 * Block matching used by the file-transfer code.
 *
 * Copyright (C) 1996 Andrew Tridgell
 * Copyright (C) 1996 Paul Mackerras
 * Copyright (C) 2003-2009 Wayne Davison
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

extern int verbose;
extern int do_progress;
extern int checksum_seed;
extern int append_mode;

int updating_basis_file;

static int false_alarms;
static int hash_hits;
static int matches;
static int64 data_transfer;

static int total_false_alarms;
static int total_hash_hits;
static int total_matches;

extern struct stats stats;

#define TRADITIONAL_TABLESIZE (1<<16)

static uint32 tablesize;
static int32 *hash_table;

#define SUM2HASH2(s1,s2) (((s1) + (s2)) & 0xFFFF)
#define SUM2HASH(sum) SUM2HASH2((sum)&0xFFFF,(sum)>>16)

#define BIG_SUM2HASH(sum) ((sum)%tablesize)

static void build_hash_table(struct sum_struct *s)
{
	static uint32 alloc_size;
	int32 i;

	/* Dynamically calculate the hash table size so that the hash load
	 * for big files is about 80%.  A number greater than the traditional
	 * size must be odd or s2 will not be able to span the entire set. */
	tablesize = (uint32)(s->count/8) * 10 + 11;
	if (tablesize < TRADITIONAL_TABLESIZE)
		tablesize = TRADITIONAL_TABLESIZE;
	if (tablesize > alloc_size || tablesize < alloc_size - 16*1024) {
		if (hash_table)
			free(hash_table);
		hash_table = new_array(int32, tablesize);
		if (!hash_table)
			out_of_memory("build_hash_table");
		alloc_size = tablesize;
	}

	memset(hash_table, 0xFF, tablesize * sizeof hash_table[0]);

	if (tablesize == TRADITIONAL_TABLESIZE) {
		for (i = 0; i < s->count; i++) {
			uint32 t = SUM2HASH(s->sums[i].sum1);
			s->sums[i].chain = hash_table[t];
			hash_table[t] = i;
		}
	} else {
		for (i = 0; i < s->count; i++) {
			uint32 t = BIG_SUM2HASH(s->sums[i].sum1);
			s->sums[i].chain = hash_table[t];
			hash_table[t] = i;
		}
	}
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
static void matched(int f, struct sum_struct *s, struct map_struct *buf,
		    OFF_T offset, int32 i)
{
	int32 n = (int32)(offset - last_match); /* max value: block_size (int32) */
	int32 j;

	if (verbose > 2 && i >= 0) {
		rprintf(FINFO,
			"match at %.0f last_match=%.0f j=%d len=%ld n=%ld\n",
			(double)offset, (double)last_match, i,
			(long)s->sums[i].len, (long)n);
	}

	send_token(f, i, buf, last_match, n, i < 0 ? 0 : s->sums[i].len);
	data_transfer += n;

	if (i >= 0) {
		stats.matched_data += s->sums[i].len;
		n += s->sums[i].len;
	}

	for (j = 0; j < n; j += CHUNK_SIZE) {
		int32 n1 = MIN(CHUNK_SIZE, n - j);
		sum_update(map_ptr(buf, last_match + j, n1), n1);
	}

	if (i >= 0)
		last_match = offset + s->sums[i].len;
	else
		last_match = offset;

	if (buf && do_progress)
		show_progress(last_match, buf->file_size);
}


static void hash_search(int f,struct sum_struct *s,
			struct map_struct *buf, OFF_T len)
{
	OFF_T offset, end;
	int32 k, want_i, backup;
	char sum2[SUM_LENGTH];
	uint32 s1, s2, sum;
	int more;
	schar *map;

	/* want_i is used to encourage adjacent matches, allowing the RLL
	 * coding of the output to work more efficiently. */
	want_i = 0;

	if (verbose > 2) {
		rprintf(FINFO, "hash search b=%ld len=%.0f\n",
			(long)s->blength, (double)len);
	}

	k = (int32)MIN(len, (OFF_T)s->blength);

	map = (schar *)map_ptr(buf, 0, k);

	sum = get_checksum1((char *)map, k);
	s1 = sum & 0xFFFF;
	s2 = sum >> 16;
	if (verbose > 3)
		rprintf(FINFO, "sum=%.8x k=%ld\n", sum, (long)k);

	offset = 0;

	end = len + 1 - s->sums[s->count-1].len;

	if (verbose > 3) {
		rprintf(FINFO, "hash search s->blength=%ld len=%.0f count=%.0f\n",
			(long)s->blength, (double)len, (double)s->count);
	}

	do {
		int done_csum2 = 0;
		int32 i;

		if (verbose > 4) {
			rprintf(FINFO, "offset=%.0f sum=%04x%04x\n",
				(double)offset, s2 & 0xFFFF, s1 & 0xFFFF);
		}

		if (tablesize == TRADITIONAL_TABLESIZE) {
			if ((i = hash_table[SUM2HASH2(s1,s2)]) < 0)
				goto null_hash;
			sum = (s1 & 0xffff) | (s2 << 16);
		} else {
			sum = (s1 & 0xffff) | (s2 << 16);
			if ((i = hash_table[BIG_SUM2HASH(sum)]) < 0)
				goto null_hash;
		}

		hash_hits++;
		do {
			int32 l;

			if (sum != s->sums[i].sum1)
				continue;

			/* also make sure the two blocks are the same length */
			l = (int32)MIN((OFF_T)s->blength, len-offset);
			if (l != s->sums[i].len)
				continue;

			/* in-place: ensure chunk's offset is either >= our
			 * offset or that the data didn't move. */
			if (updating_basis_file && s->sums[i].offset < offset
			    && !(s->sums[i].flags & SUMFLG_SAME_OFFSET))
				continue;

			if (verbose > 3) {
				rprintf(FINFO,
					"potential match at %.0f i=%ld sum=%08x\n",
					(double)offset, (long)i, sum);
			}

			if (!done_csum2) {
				map = (schar *)map_ptr(buf,offset,l);
				get_checksum2((char *)map,l,sum2);
				done_csum2 = 1;
			}

			if (memcmp(sum2,s->sums[i].sum2,s->s2length) != 0) {
				false_alarms++;
				continue;
			}

			/* When updating in-place, the best possible match is
			 * one with an identical offset, so we prefer that over
			 * the following want_i optimization. */
			if (updating_basis_file) {
				int32 i2;
				for (i2 = i; i2 >= 0; i2 = s->sums[i2].chain) {
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
				}
			}

			/* we've found a match, but now check to see
			 * if want_i can hint at a better match. */
			if (i != want_i && want_i < s->count
			    && (!updating_basis_file || s->sums[want_i].offset >= offset
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
			k = (int32)MIN((OFF_T)s->blength, len-offset);
			map = (schar *)map_ptr(buf, offset, k);
			sum = get_checksum1((char *)map, k);
			s1 = sum & 0xFFFF;
			s2 = sum >> 16;
			matches++;
			break;
		} while ((i = s->sums[i].chain) >= 0);

	  null_hash:
		backup = (int32)(offset - last_match);
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
		if (backup >= s->blength+CHUNK_SIZE && end-offset > CHUNK_SIZE)
			matched(f, s, buf, offset - s->blength, -2);
	} while (++offset < end);

	matched(f, s, buf, len, -1);
	map_ptr(buf, len-1, 1);
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
	char file_sum[MAX_DIGEST_LEN];
	int sum_len;

	last_match = 0;
	false_alarms = 0;
	hash_hits = 0;
	matches = 0;
	data_transfer = 0;

	sum_init(checksum_seed);

	if (append_mode > 0) {
		if (append_mode == 2) {
			OFF_T j = 0;
			for (j = CHUNK_SIZE; j < s->flength; j += CHUNK_SIZE) {
				if (buf && do_progress)
					show_progress(last_match, buf->file_size);
				sum_update(map_ptr(buf, last_match, CHUNK_SIZE),
					   CHUNK_SIZE);
				last_match = j;
			}
			if (last_match < s->flength) {
				int32 n = (int32)(s->flength - last_match);
				if (buf && do_progress)
					show_progress(last_match, buf->file_size);
				sum_update(map_ptr(buf, last_match, n), n);
			}
		}
		last_match = s->flength;
		s->count = 0;
	}

	if (len > 0 && s->count > 0) {
		build_hash_table(s);

		if (verbose > 2)
			rprintf(FINFO,"built hash table\n");

		hash_search(f, s, buf, len);

		if (verbose > 2)
			rprintf(FINFO,"done hash search\n");
	} else {
		OFF_T j;
		/* by doing this in pieces we avoid too many seeks */
		for (j = last_match + CHUNK_SIZE; j < len; j += CHUNK_SIZE)
			matched(f, s, buf, j, -2);
		matched(f, s, buf, len, -1);
	}

	sum_len = sum_end(file_sum);
	/* If we had a read error, send a bad checksum. */
	if (buf && buf->status != 0)
		file_sum[0]++;

	if (verbose > 2)
		rprintf(FINFO,"sending file_sum\n");
	write_buf(f, file_sum, sum_len);

	if (verbose > 2)
		rprintf(FINFO, "false_alarms=%d hash_hits=%d matches=%d\n",
			false_alarms, hash_hits, matches);

	total_hash_hits += hash_hits;
	total_false_alarms += false_alarms;
	total_matches += matches;
	stats.literal_data += data_transfer;
}

void match_report(void)
{
	if (verbose <= 1)
		return;

	rprintf(FINFO,
		"total: matches=%d  hash_hits=%d  false_alarms=%d data=%.0f\n",
		total_matches, total_hash_hits, total_false_alarms,
		(double)stats.literal_data);
}
