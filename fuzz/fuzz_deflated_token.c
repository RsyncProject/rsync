/*
 * fuzz_deflated_token.c - libFuzzer harness for rsync's zlib compressed-token
 * decoder: recv_deflated_token() in token.c (reference.md Part 3.3, lines
 * 552-664), reached via recv_token() when do_compression == CPRES_ZLIB.
 *
 * This closes the coverage gap left by fuzz_token.c, which only drives the
 * uncompressed simple_recv_token() path. The compressed path is the same
 * untrusted-peer wire-parsing surface as CVE-2026-43618 (a malicious *sender*
 * drives the receiver's decode loop): it reads attacker-controlled flag bytes,
 * a 14-bit DEFLATED_DATA length, absolute/relative token numbers and run
 * counts, and feeds the byte stream through zlib inflate() into fixed
 * cbuf[MAX_DATA_COUNT] / dbuf[AVAIL_OUT_SIZE(CHUNK_SIZE)] buffers.
 *
 * LIVE objects (NOT stubbed): the harness links the REAL instrumented token.o
 * (compiled as token_fuzz.o with -DRSYNC_FUZZ_TOKEN, which only adds the thin
 * ifdef hook below the parser - no parser/bound/inflate logic is altered), the
 * REAL io.o readers (read_byte/read_buf/read_int -> safe_read), and rsync's
 * REAL bundled zlib objects (inflate.o, inffast.o, inftrees.o, ...). The
 * decode/inflate/bounds/run-accounting code all runs for real and is
 * sanitizer-instrumented; masking any of it would hide the very bugs we hunt.
 * Only true process-boundary externals (exit_cleanup, logging, allocator) are
 * shimmed in fuzz/stubs.c, and exit_cleanup longjmps back here so a *correctly
 * rejected* hostile input is not counted as a crash (a real OOB trips ASan
 * BEFORE any guard fires - oracle fidelity preserved).
 *
 * Decompressor init fidelity: we do NOT hand-roll inflateInit2. The real
 * receiver initializes rx_strm lazily inside recv_deflated_token's r_init arm
 * (inflateInit2(&rx_strm, -15), then inflateReset on subsequent streams). The
 * harness simply forces recv_state=r_init between inputs via the reset hook, so
 * the decompressor is set up EXACTLY as the real receiver sets it up - any
 * crash is a real parser/inflate bug, not a harness mis-init.
 *
 * Plumbing: identical fd trick to fuzz_io/fuzz_token - fuzz bytes arrive via a
 * pipe and io.c's readers take the non-iobuf safe_read() fast path.
 */

#include "rsync.h"
#include <setjmp.h>
#include <unistd.h>
#include <fcntl.h>

extern int32 fuzz_recv_deflated_token(int f, char **data);
extern void fuzz_recv_deflated_token_reset(void);

extern int do_compression;
extern jmp_buf fuzz_unwind_env;
extern int fuzz_unwind_armed;
extern int protocol_version;

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

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	if (size < 1)
		return 0;

	protocol_version = 30;
	do_compression = CPRES_ZLIB;	/* recv_deflated_token path */

	/* Restore the decompressor to a fresh-receiver state for THIS input. The
	 * next decode call re-enters the r_init arm (inflateReset), mirroring the
	 * real receiver's per-transfer init. */
	fuzz_recv_deflated_token_reset();

	int f = fd_from_bytes(data, size);
	if (f < 0)
		return 0;

	fuzz_unwind_armed = 1;
	if (setjmp(fuzz_unwind_env) == 0) {
		char *out;
		int32 n;
		/* Drive the decoder over the whole stream: each call returns a
		 * positive literal byte count, a negative token index, or 0 at
		 * END_FLAG (clean end). Bounded loop guards a pathological spin.
		 * A malformed stream (bad inflate, over-range token/run) makes a
		 * guard call exit_cleanup, which longjmps out - not a crash. */
		for (int i = 0; i < 1 << 20; i++) {
			n = fuzz_recv_deflated_token(f, &out);
			if (n == 0)
				break;	/* END_FLAG: stream finished cleanly */
			(void)out;
		}
	}
	fuzz_unwind_armed = 0;

	close(f);
	return 0;
}
