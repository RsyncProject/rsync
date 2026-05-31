/*
 * fuzz_xattrs.c - STAGED STUB (not yet wired into fuzz/Makefile).
 *
 * Intended target: receive_xattr() (xattrs.c ~771-877) - the per-file xattr
 * wire decode (reference.md Part 3.4). The actual fuzz-worthy region is the
 * bounded-read loop, lines 780-820:
 *     ndx        = read_varint(f)                                   (guard 782)
 *     count      = read_varint_bounded(f, 0, MAX_WIRE_XATTR_COUNT)  (793)
 *     name_len   = read_varint_size(f, MAX_WIRE_XATTR_NAMELEN)      (802)
 *     datum_len  = read_varint_size(f, MAX_WIRE_XATTR_DATALEN)      (803)
 *     overflow guard: SIZE_MAX - dget_len < extra_len || ...        (806)
 *     read_buf(name), trailing-'\0' check                          (810-814)
 *     read_buf(datum / abbrev checksum)                            (815-819)
 *
 * WHY THIS IS A STUB (honest blocker):
 *   receive_xattr is one function: after the parse loop it unconditionally calls
 *   rsync_xal_store(), which reaches xattr_lookup_hash() -> sum_init/sum_update/
 *   sum_end (the checksum subsystem: openssl/xxhash/md*, ~all of checksum.o) and
 *   hashtable_create/hashtable_find (hashtable.o). It also calls f_name() and,
 *   if saw_xattr_filter, name_is_excluded() (exclude.o + filter state). We cannot
 *   exercise the bounded-read region without also linking that storage tail.
 *   Linking checksum.o pulls a large, mostly-irrelevant surface; stubbing
 *   sum_*/hashtable_* risks masking an OOB that occurs in the abbreviated-datum
 *   (XSTATE_ABBREV) checksum copy at line 819. Out of WS2 budget after io + token.
 *
 * REQUIRED GLOBAL INIT when completed (reference.md Part 3.5):
 *   protocol_version, xfer_sum_len, xattr_sum_len (used by the abbrev-datum
 *   branch, line 819), preserve_xattrs = 2, saw_xattr_filter = 0 (to skip the
 *   exclude.o path), am_root, file_extra_cnt + xattrs_ndx (for F_XATTR slot),
 *   and rsync_xal_l / rsync_xal_h reset to empty between inputs (statics:
 *   document the cross-input coupling as in fuzz_token).
 *
 * Strategy to finish (sketch): link xattrs.o + io.o + hashtable.o + checksum.o
 *   (+ its crypto deps) OR provide faithful sum_*/hashtable_* shims that still
 *   let ASan see every wire-driven allocation and the line-819 abbrev copy.
 *   Set saw_xattr_filter = 0 so name_is_excluded()/exclude.o is never reached.
 */

#include <stdint.h>
#include <stddef.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	(void)data; (void)size;
	return 0;	/* no-op until the storage-tail link surface is built out */
}
