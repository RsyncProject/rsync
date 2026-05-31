/*
 * fuzz_xattrs.c - LIVE libFuzzer harness for rsync's receive_xattr() (xattrs.c
 * ~774-880): the per-file xattr wire decode (reference.md Part 3.4). The
 * fuzz-worthy region is the bounded-read loop (xattrs.c 802-872):
 *     ndx       = read_varint(f)                                    (783)
 *     count     = read_varint_bounded(f, 0, MAX_WIRE_XATTR_COUNT)   (796)
 *     name_len  = read_varint_size(f, MAX_WIRE_XATTR_NAMELEN)       (805)
 *     datum_len = read_varint_size(f, MAX_WIRE_XATTR_DATALEN)       (806)
 *     overflow guard (SIZE_MAX - dget_len < extra_len || ...)       (809)
 *     read_buf(name) + trailing-'\0' check                         (813-817)
 *     read_buf(datum / abbreviated checksum)                       (818-823)
 * then rsync_xal_store() (real: hashtable + checksum subsystem).
 *
 * LIVE LINK: xattrs.o (compiled -DRSYNC_FUZZ_XATTRS) + the SAME real core as
 * fuzz_flist (io/util1/util2/uidlist/exclude/hashtable/checksum/flist/acls/
 * fileio/syscall + lib/*). Only true process-boundary externals are stubbed.
 * NOTHING in the xattr parse/alloc/copy/hash path is stubbed.
 *
 * PLUMBING: same pipe-fd fast path as fuzz_flist (read_buf -> safe_read off the
 * attacker bytes; EOF -> exit_cleanup -> longjmp).
 *
 * INIT (reference.md Part 3.5): protocol_version, xfer_sum_len + xattr_sum_len
 * (the abbreviated-datum branch at 822 copies xattr_sum_len bytes),
 * preserve_xattrs (1 or 2 - 2 enables rsync.%FOO names), saw_xattr_filter=0 so
 * name_is_excluded()/exclude filter state is never reached, am_root (gates the
 * HAS_PREFIX namespace-rewrite branches), and xattrs_ndx for the F_XATTR slot.
 *
 * ORACLE: a correctly-rejected hostile value (out-of-range ndx/count, bad
 * trailing NUL, overflow) calls exit_cleanup/overflow_exit AFTER its guard =>
 * clean longjmp, not a finding. A genuine OOB/UB in a copy or pointer-arith
 * step trips ASan/UBSan BEFORE any guard => real finding.
 */

#include "rsync.h"
#include <setjmp.h>
#include <unistd.h>
#include <fcntl.h>

extern void fuzz_receive_xattr(int f, struct file_struct *file);
extern struct file_struct *fuzz_xattr_file_new(alloc_pool_t pool);

/* Reuse fuzz_flist's pool helpers for a throwaway flist + pool. */
extern struct file_list *fuzz_flist_new(void);
extern void fuzz_flist_free(struct file_list *flist);

extern jmp_buf fuzz_unwind_env;
extern int fuzz_unwind_armed;

extern int protocol_version, preserve_xattrs, saw_xattr_filter, am_root;
extern int xattr_sum_len, xfer_sum_len, file_extra_cnt, xattrs_ndx;
extern int numeric_ids, inc_recurse, am_sender, am_server;

void parse_checksum_choice(int);
void init_flist(void);

static int fd_from_bytes(const uint8_t *data, size_t size)
{
	int fds[2];
	if (pipe(fds) != 0)
		return -1;
	fcntl(fds[1], F_SETFL, O_NONBLOCK);
	size_t off = 0;
	while (off < size) {
		ssize_t n = write(fds[1], data + off, size - off);
		if (n <= 0)
			break;
		off += (size_t)n;
	}
	close(fds[1]);
	return fds[0];
}

static int inited;

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	if (size < 1)
		return 0;

	uint8_t sel = data[0];
	static const int protos[] = { 28, 29, 30, 31 };
	protocol_version = protos[(sel >> 1) & 3];
	preserve_xattrs = (sel & 0x01) ? 2 : 1;	/* >=1 required; 2 enables %FOO names */
	am_root = (sel & 0x04) ? 1 : 0;		/* gates namespace-prefix rewrite branches */
	saw_xattr_filter = 0;			/* keep name_is_excluded()/filter state out */
	numeric_ids = 1;
	inc_recurse = 0;
	am_sender = 0;
	am_server = 0;

	if (!inited) {
		parse_checksum_choice(0);	/* sets xattr_sum_len via file/xfer sums */
		/* setup_protocol assigns xattrs_ndx; receive_xattr writes F_XATTR via
		 * it. As receiver with only -X: a single trailing extra slot. */
		file_extra_cnt = 1;
		xattrs_ndx = 1;
		inited = 1;
	}

	const uint8_t *body = data + 1;
	size_t bodysz = size - 1;

	int f = fd_from_bytes(body, bodysz);
	if (f < 0)
		return 0;

	struct file_list *flist = fuzz_flist_new();
	struct file_struct *file = fuzz_xattr_file_new(flist->file_pool);

	fuzz_unwind_armed = 1;
	if (setjmp(fuzz_unwind_env) == 0) {
		/* Drive a sequence of receive_xattr calls from one input so the
		 * rsync_xal_l accumulation / find_matching_xattr dedup path (a prime
		 * bug site) is exercised across entries. */
		for (int i = 0; i < 32; i++)
			fuzz_receive_xattr(f, file);
	}
	fuzz_unwind_armed = 0;

	fuzz_flist_free(flist);
	close(f);
	return 0;
}
