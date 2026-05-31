/*
 * fuzz_token.c - libFuzzer harness for rsync's token/delta decode (token.c).
 *
 * Target: recv_token() with do_compression == CPRES_NONE, i.e.
 *   simple_recv_token() (reference.md Part 3.3, lines 282-311). Headline guard:
 *   the "i > CHUNK_SIZE" length check (token.c ~line 298) that stops a hostile
 *   peer from driving read_buf() past the static CHUNK_SIZE literal buffer.
 *
 * Plumbing: identical fd trick to fuzz_io - bytes arrive via a pipe and the
 * io.c readers take the non-iobuf safe_read() fast path. recv_token pulls a
 * 4-byte length via read_int then a literal run via read_buf.
 *
 * STATE CAVEAT (documented, not a defect): simple_recv_token keeps a file-local
 *   `residue`/`buf`. We drive recv_token in a loop until it returns <= 0 (a
 *   clean chunk boundary where residue==0), so most iterations leave residue==0.
 *   If an iteration unwinds mid-run (EOF/guard longjmp), residue can carry into
 *   the next input; that cannot cause a FALSE crash (the i>CHUNK_SIZE guard and
 *   read_buf's own length still bound every access) - it only adds harmless
 *   cross-input coupling. The CPRES_ZLIB (recv_deflated_token) path is NOT wired
 *   here precisely because its zlib stream state is not externally resettable
 *   between iterations; see fuzz/README.md.
 *
 * Globals: do_compression set per-input; LZ4/ZSTD entry points are stubbed in
 *   fuzz/stubs.c (never reached under CPRES_NONE) so the object links.
 */

#include "rsync.h"
#include <setjmp.h>
#include <unistd.h>
#include <fcntl.h>

extern int32 recv_token(int f, char **data);
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
	do_compression = CPRES_NONE;	/* simple_recv_token path */

	int f = fd_from_bytes(data, size);
	if (f < 0)
		return 0;

	fuzz_unwind_armed = 1;
	if (setjmp(fuzz_unwind_env) == 0) {
		char *out;
		int32 n;
		/* Consume the whole stream as a sequence of literal tokens until a
		 * clean end (<=0) or EOF longjmp. Bounded loop guards against a
		 * pathological 0-length spin. */
		for (int i = 0; i < 1 << 20; i++) {
			n = recv_token(f, &out);
			if (n <= 0)
				break;
		}
	}
	fuzz_unwind_armed = 0;

	close(f);
	return 0;
}
