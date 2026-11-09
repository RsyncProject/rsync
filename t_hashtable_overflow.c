/*
 * Regression harness for the hashtable size*node_size integer overflow
 * (Leonid Bugaev May-2026 re-audit, KI-11/12).
 *
 * hashtable_create() once computed the slot-array byte count as
 * new_array0(char, size * node_size) in 32-bit int arithmetic; for a large
 * peer/data-driven size the product wrapped to a tiny value, under-allocating
 * the table while tbl->size recorded the huge size -- a later node access then
 * ran out of bounds (heap overflow / SEGV).  The fix passes size and node_size
 * as separate factors so my_alloc's --max-alloc guard rejects the oversized
 * request, exiting RERR_MALLOC instead of under-allocating.
 *
 * This harness sets a realistic max_alloc (t_stub leaves it at SIZE_MAX) and
 * asks for an absurd size: the fixed code exits RERR_MALLOC; a regressed build
 * under-allocates and crashes on the node access below.  Not linked into rsync.
 *
 * This program is free software; you can redistribute it and/or modify it under
 * the terms of the GNU General Public License version 3 as published by the
 * Free Software Foundation.
 */

#include "rsync.h"

extern size_t max_alloc;	/* defined in util2.o/t_stub.o */
short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];	/* for DEBUG_GTE in hashtable.o */

int main(UNUSED(int argc), UNUSED(char *argv[]))
{
	struct hashtable *tbl;
	int i;

	/* A realistic --max-alloc cap (the default is 1 GiB) so my_alloc's guard
	 * can engage; t_stub.o leaves max_alloc == SIZE_MAX. */
	max_alloc = (size_t)1024 * 1024 * 1024;

	/* 2^28 buckets * 16-byte node = 2^32 bytes: the product wraps int to ~0 in
	 * the unfixed code.  The fix must reject this (exit RERR_MALLOC) rather than
	 * under-allocate. */
	tbl = hashtable_create(1 << 28, 0);

	/* Unreachable with the fix (hashtable_create exits above).  If a regression
	 * lets it return, touch a node near the claimed end -- an under-allocated
	 * table faults here -- and report the unexpected survival as a failure. */
	for (i = 0; i < tbl->size; i += tbl->size / 64 + 1) {
		struct ht_int32_node *node = HT_NODE(tbl, tbl->nodes, i);
		node->key = i;
	}
	fprintf(stderr, "FAIL: hashtable_create(1<<28) was not rejected\n");
	return 1;
}
