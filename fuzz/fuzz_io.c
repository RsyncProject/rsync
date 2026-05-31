/*
 * fuzz_io.c - libFuzzer harness for rsync's io.c wire-reading primitives.
 *
 * Targets (all UNTRUSTED-PEER parsers, reference.md Part 3.1):
 *   read_sum_head, read_varint, read_varlong, read_longint, read_vstring,
 *   read_int_bounded, read_varint_bounded, read_varint_size, read_int, read_byte.
 *
 * PLUMBING (the load-bearing trick):
 *   io.c's read_buf(f, buf, len) has a fast path:
 *       if (f != iobuf.in_fd) { safe_read(f, buf, len); ... return; }
 *   i.e. when the fd is NOT the registered multiplexed input fd, every reader
 *   pulls bytes straight from that fd via safe_read()/read(2) - no iobuf, no
 *   multiplexing, no msg framing. We exploit that: iobuf stays at its default
 *   ({.in_fd = -1}), and we hand the readers a fd backed by the fuzz buffer.
 *   So real parser logic + real guards run, with bytes 100% attacker-chosen.
 *
 *   The fuzz buffer is delivered through a pipe: write all bytes, close the
 *   write end => reads drain the buffer then hit EOF (read()==0). On EOF
 *   safe_read returns short, read_buf calls whine_about_eof()->exit_cleanup(),
 *   which our stub turns into a longjmp back here. So "ran out of bytes" is a
 *   clean unwind, never a crash.
 *
 * ORACLE FIDELITY:
 *   A correctly-rejected hostile value (over-range count/length/etc.) calls
 *   exit_cleanup() AFTER the guard => clean longjmp, not a finding. A genuine
 *   OOB read/write or UB happens during the read itself and trips ASan/UBSan
 *   BEFORE any guard => real finding. The stubs never mask memory errors.
 *
 * GLOBAL INIT CONTRACT (reference.md Part 3.5):
 *   protocol_version, xfer_sum_len, and the log-level arrays are provided by
 *   fuzz/stubs.c. The first byte of each input selects protocol_version and
 *   xfer_sum_len so a single corpus exercises the proto<27 / <30 / >=30
 *   branches of read_sum_head and the varint30 width choices.
 */

#include "rsync.h"
#include <setjmp.h>
#include <unistd.h>
#include <fcntl.h>

/* From io.c (unmodified object under test). */
extern int32 read_int(int f);
extern int32 read_varint(int f);
extern int64 read_varlong(int f, uchar min_bytes);
extern int64 read_longint(int f);
extern int read_vstring(int f, char *buf, int bufsize);
extern int32 read_int_bounded(int f, int32 lo, int32 hi, const char *what);
extern int32 read_varint_bounded(int f, int32 lo, int32 hi, const char *what);
extern size_t read_varint_size(int f, size_t max, const char *what);
extern uchar read_byte(int f);
extern void read_sum_head(int f, struct sum_struct *sum);

/* From fuzz/stubs.c */
extern jmp_buf fuzz_unwind_env;
extern int fuzz_unwind_armed;
extern int protocol_version;
extern int xfer_sum_len;

/* Open a read fd backed by the given bytes: write to a pipe, close write end. */
static int fd_from_bytes(const uint8_t *data, size_t size)
{
	int fds[2];
	if (pipe(fds) != 0)
		return -1;
	/* For inputs larger than the pipe buffer we'd block on write; cap the
	 * payload to a generous bound (parsers never legitimately need more in
	 * one call than this, and the corpus stays small/fast). */
	size_t off = 0;
	/* Make the write end non-blocking so an over-large input can't deadlock;
	 * we simply stop feeding once the pipe is full - the reader hits EOF
	 * after consuming what fit, which is fine for fuzzing. */
	fcntl(fds[1], F_SETFL, O_NONBLOCK);
	while (off < size) {
		ssize_t n = write(fds[1], data + off, size - off);
		if (n <= 0)
			break;
		off += (size_t)n;
	}
	close(fds[1]);
	return fds[0];
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	if (size < 1)
		return 0;

	/* Byte 0 chooses protocol + digest length to cover all branches. */
	uint8_t sel = data[0];
	static const int protos[] = { 20, 26, 29, 30, 31 };
	protocol_version = protos[(sel >> 1) % 5];
	/* xfer_sum_len in a realistic range (4..32); read_sum_head bounds s2length
	 * against it, and the multiply-overflow guard uses it. */
	xfer_sum_len = 4 + (sel & 0x1f);
	if (xfer_sum_len > 32)
		xfer_sum_len = 32;

	data++; size--;

	int f = fd_from_bytes(data, size);
	if (f < 0)
		return 0;

	fuzz_unwind_armed = 1;
	if (setjmp(fuzz_unwind_env) == 0) {
		/* Drive a sequence of readers off the same byte stream. Order is
		 * arbitrary; whichever consumes the bytes first wins, the rest hit
		 * EOF and longjmp out. read_sum_head is the headline target. */
		struct sum_struct sum;
		read_sum_head(f, &sum);

		(void)read_varint(f);
		(void)read_varlong(f, 3);
		(void)read_longint(f);
		(void)read_int_bounded(f, -1000, 1000, "fuzz int");
		(void)read_varint_bounded(f, 0, 0x7fffffff, "fuzz varint");
		(void)read_varint_size(f, MAXPATHLEN, "fuzz size");

		char vbuf[MAXPATHLEN];
		(void)read_vstring(f, vbuf, sizeof vbuf);
	}
	fuzz_unwind_armed = 0;

	close(f);
	return 0;
}
