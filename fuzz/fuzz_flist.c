/*
 * fuzz_flist.c - LIVE libFuzzer harness for rsync's recv_file_entry() (flist.c).
 *
 * TARGET: recv_file_entry (flist.c ~682-1229) - the sender->receiver file-list
 * entry wire decode, with the stateful static lastname[]/lastdir reconstruction
 * (reference.md Part 3.2). recv_file_entry is file-static, so flist.c is compiled
 * with -DRSYNC_FUZZ_FLIST which appends three thin wrappers (fuzz_flist_new /
 * fuzz_recv_file_entry / fuzz_flist_free) that replicate EXACTLY the per-entry
 * work recv_file_list does around the parser (flist_expand + append). The heavy,
 * non-target tail (sort/clean/recv_id_list) is intentionally skipped.
 *
 * LIVE LINK: this harness links the REAL instrumented flist.o + io.o + util1.o +
 * util2.o + uidlist.o + exclude.o + hashtable.o + lib/*.o (pool_alloc, wildmatch,
 * mdigest, ...) + checksum.o. Only true process-boundary externals (logging,
 * exit_cleanup->longjmp, terminal/socket I/O) are stubbed. NOTHING in the
 * parse/alloc/copy path is stubbed.
 *
 * PLUMBING: identical fast-path trick as fuzz_io - iobuf stays default
 * ({.in_fd=-1}), the fd we hand the parser is a pipe, so every read_buf/read_sbuf
 * takes the safe_read() path straight off attacker bytes. EOF => safe_read short
 * => whine_about_eof -> exit_cleanup -> longjmp back here (clean unwind).
 *
 * STATEFULNESS: recv_file_entry's lastname[]/lastdir/mode/uid/... are function
 * statics persisting across entries; XMIT_SAME_NAME reuses l1 bytes of lastname.
 * We drive a SEQUENCE of entries from one input so the cross-entry name/dir
 * reconstruction (a prime bug site) is exercised. State leaks across fuzzer
 * inputs too (cannot reset function statics from outside) - documented, same as
 * fuzz_token.
 *
 * ORACLE: a correctly-rejected hostile value calls overflow_exit/exit_cleanup
 * AFTER its guard => clean longjmp, not a finding. A genuine OOB/UB during a copy
 * or read trips ASan/UBSan BEFORE any guard => real finding.
 */

#include "rsync.h"
#include <setjmp.h>
#include <unistd.h>
#include <fcntl.h>

extern struct file_list *fuzz_flist_new(void);
extern struct file_struct *fuzz_recv_file_entry(int f, struct file_list *flist, int xflags);
extern void fuzz_flist_free(struct file_list *flist);

extern uchar read_byte(int f);

/* From fuzz/stubs.c */
extern jmp_buf fuzz_unwind_env;
extern int fuzz_unwind_armed;

/* Globals recv_file_entry consults. */
extern int protocol_version;
extern int preserve_links, preserve_devices, preserve_specials, preserve_hard_links;
extern int preserve_uid, preserve_gid, preserve_acls, preserve_xattrs;
extern int relative_paths, sanitize_paths, munge_symlinks;
extern int atimes_ndx, uid_ndx, gid_ndx, acls_ndx, xattrs_ndx;
extern int crtimes_ndx, pathname_ndx, depth_ndx, unsort_ndx;
extern int preserve_atimes, preserve_crtimes;
extern int file_extra_cnt;
extern int numeric_ids, inc_recurse, am_root, always_checksum;
extern int xfer_dirs, recurse, one_file_system, copy_devices;
extern int trust_sender_filter;

void init_flist(void);            /* sets flist_csum_len from file_sum_nni */
void parse_checksum_choice(int);  /* sets file_sum_nni/xfer_sum_nni (negotiation) */

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
	if (size < 2)
		return 0;

	/* Byte 0: protocol + feature selection (cover proto<28 / <30 / >=30 and
	 * the optional-field branches that change which reads happen). */
	uint8_t sel = data[0];
	static const int protos[] = { 26, 28, 29, 30, 31 };
	protocol_version = protos[(sel >> 1) % 5];

	preserve_links    = (sel & 0x01) ? 1 : 0;
	preserve_devices  = (sel & 0x02) ? 1 : 0;
	preserve_specials = (sel & 0x04) ? 1 : 0;
	preserve_uid      = (sel & 0x08) ? 1 : 0;
	preserve_gid      = (sel & 0x10) ? 1 : 0;
	preserve_hard_links = (sel & 0x20) ? 1 : 0;
	always_checksum   = (sel & 0x40) ? 1 : 0;
	/* Keep acls/xattrs OFF here: those tails have dedicated harnesses
	 * (fuzz_xattrs) and would otherwise dominate this corpus. */
	preserve_acls = 0;
	preserve_xattrs = 0;

	/* Replicate setup_protocol()'s extra-slot assignment EXACTLY (compat.c
	 * 575-595). The *_ndx values and file_extra_cnt are NOT free parameters:
	 * 64-bit fields (atime) must be assigned first so their slot is 8-byte
	 * aligned. Arbitrary ndx/cnt combinations produce misaligned F_ATIME
	 * accesses that the real receiver never generates (a harness artifact,
	 * not a recv_file_entry bug). We are the receiver: am_sender=0, am_server=0. */
	preserve_atimes  = (data[1] & 0x01) ? 1 : 0;
	preserve_crtimes = 0;            /* SUPPORT_CRTIMES not configured */
	atimes_ndx = crtimes_ndx = pathname_ndx = depth_ndx = 0;
	uid_ndx = gid_ndx = acls_ndx = xattrs_ndx = unsort_ndx = 0;
	file_extra_cnt = 0;
	if (preserve_atimes)
		atimes_ndx = (file_extra_cnt += EXTRA64_CNT);
	/* am_sender==0 => depth_ndx branch */
	depth_ndx = ++file_extra_cnt;
	if (preserve_uid)
		uid_ndx = ++file_extra_cnt;
	if (preserve_gid)
		gid_ndx = ++file_extra_cnt;
	if (preserve_acls)               /* (!am_sender already true) */
		acls_ndx = ++file_extra_cnt;
	if (preserve_xattrs)
		xattrs_ndx = ++file_extra_cnt;

	relative_paths = (data[1] & 0x10) ? 1 : 0;
	sanitize_paths = (data[1] & 0x20) ? 1 : 0;
	munge_symlinks = 0;          /* keep SYMLINK_PREFIX math out unless wanted */
	numeric_ids = 1;
	inc_recurse = 0;
	am_root = 0;
	one_file_system = 0;
	copy_devices = 0;
	trust_sender_filter = 1;     /* skip check_server_filter (exclude path) */
	xfer_dirs = 1;
	recurse = 0;

	if (!inited) {
		/* Mirror the receiver's checksum negotiation: parse_checksum_choice
		 * populates file_sum_nni/xfer_sum_nni (NULL choice => protocol default)
		 * which init_flist then consults for flist_csum_len. */
		parse_checksum_choice(0);
		init_flist();            /* sets flist_csum_len from file_sum_nni */
		/* Allocate the hard-link dev/inode table ONCE. recv_file_entry's
		 * proto<30 hard-link path (flist.c:1191) calls idev_find(), which needs
		 * dev_tbl. init_hard_links only creates it when protocol_version<30, so
		 * we force that here. We never idev_destroy() between inputs: idev_find
		 * keeps a static dev_node pointer into dev_tbl that we cannot reset from
		 * outside, so the table is process-lived (benign cross-input coupling,
		 * documented like the lastname[] statics). */
		int saved = protocol_version;
		protocol_version = 26;
		init_hard_links();
		protocol_version = saved;
		inited = 1;
	}

	const uint8_t *body = data + 2;
	size_t bodysz = size - 2;

	int f = fd_from_bytes(body, bodysz);
	if (f < 0)
		return 0;

	struct file_list *flist = fuzz_flist_new();

	fuzz_unwind_armed = 1;
	if (setjmp(fuzz_unwind_env) == 0) {
		/* Drive a sequence of entries. Each leading byte from the stream
		 * (consumed as the xflags) chains entries; we cap the count so a
		 * tiny input can't spin forever, and stop when the parser unwinds on
		 * EOF. */
		for (int i = 0; i < 64; i++) {
			int flags = read_byte(f);
			if (flags == 0)
				break;
			if (protocol_version >= 28 && (flags & XMIT_EXTENDED_FLAGS))
				flags |= read_byte(f) << 8;
			fuzz_recv_file_entry(f, flist, flags);
		}
	}
	fuzz_unwind_armed = 0;

	fuzz_flist_free(flist);
	close(f);
	return 0;
}
