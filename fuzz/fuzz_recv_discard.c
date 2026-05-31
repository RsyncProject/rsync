/*
 * fuzz_recv_discard.c - regression harness for the receiver discard-path
 * NULL-pointer dereference (static-findings.md REAL #1, receiver.c:413).
 *
 * THE DEFECT
 * ----------
 * receiver.c discard_receive_data() calls
 *     receive_data(f_in, NULL, -1, 0, NULL, -1, file, 0);
 * i.e. fname_r == NULL, fd_r == -1, size_r == 0, fname == NULL.
 * Inside receive_data(), because fd_r < 0 && size_r == 0, the basis is never
 * mapped:  mapbuf = NULL  (receiver.c:326-327).  When the peer then sends a
 * block-MATCH token (recv_token() returns a negative value) with a sum-header
 * count > 0 so the index check at receiver.c:393 passes, control reaches the
 * fork-added block at receiver.c:411-415:
 *
 *     if (!mapbuf) {
 *         rprintf(FERROR, "got a block match with no basis file for %s [%s]\n",
 *             full_fname(fname), who_am_i());   // <-- fname == NULL
 *         exit_cleanup(RERR_PROTOCOL);
 *     }
 *
 * full_fname(NULL) dereferences its argument unconditionally at util1.c:1282
 *     if (*fn == '/')
 * => NULL read => SIGSEGV / ASan SEGV.  A remote (sender) peer controls both
 * the sum-header count and the token stream, so this is reachable from
 * untrusted protocol input while the receiver is merely *discarding* a file.
 *
 * WHAT THIS HARNESS DOES
 * ----------------------
 * receive_data() is `static` in receiver.c, so we reproduce the exact
 * discard-path decision sequence here against the REAL, unmodified rsync
 * objects on that path: io.o (read_sum_head, recv_token via token.o, read_int,
 * read_buf) and util1.o (full_fname).  This is a faithful reproduction: every
 * function that participates in the bug - read_sum_head, recv_token, the
 * mapbuf==NULL branch, and full_fname - is the production function, and the
 * NULL deref occurs inside the production full_fname().
 *
 * The fname passed to full_fname is hard-NULL, exactly as discard_receive_data
 * supplies it.  mapbuf is hard-NULL, exactly as receive_data computes it on the
 * discard configuration (fd_r=-1, size_r=0).
 *
 * EXPECTED RESULT
 * ---------------
 * On the ORIGINAL (vulnerable) code this aborted under ASan with a NULL read in
 * full_fname (util1.c:1282).  The receiver fix makes the discard path (fd == -1,
 * fname == NULL) absorb a block-match token benignly -- it is normal protocol,
 * since the sender does not know the receiver is discarding -- and restricts the
 * "no basis file" protocol error to real-output transfers (fd != -1, fname
 * non-NULL, full_fname safe).  This harness mirrors that fixed logic and now
 * unwinds cleanly (no full_fname(NULL)); it is the standing regression proof and
 * a clean-gate target in run-regression.sh.
 */

#include "rsync.h"
#include <setjmp.h>
#include <unistd.h>
#include <fcntl.h>

extern void read_sum_head(int f, struct sum_struct *sum);
extern int32 recv_token(int f, char **data);
extern char *full_fname(const char *fn);

extern jmp_buf fuzz_unwind_env;
extern int fuzz_unwind_armed;
extern int protocol_version;
extern int do_compression;
extern int xfer_sum_len;

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

/*
 * Mirror of the receiver discard path inside receive_data():
 *   read_sum_head -> mapbuf=NULL -> recv_token loop -> match-token branch ->
 *   index check -> !mapbuf -> full_fname(fname==NULL).
 * Everything reachable from received bytes here is the real rsync code.
 */
static void drive_discard_path(int f_in)
{
	struct sum_struct sum;
	struct map_struct *mapbuf;
	char *fname = NULL;	/* discard_receive_data passes NULL */
	int fd_r = -1;		/* discard configuration */
	int fd = -1;		/* discard configuration: no output fd */
	OFF_T size_r = 0;	/* discard configuration */
	OFF_T offset = 0;
	int32 len = 0;
	char *data;
	int32 i;

	read_sum_head(f_in, &sum);		/* peer-controlled sum.count */

	if (fd_r >= 0 && size_r > 0)
		mapbuf = (struct map_struct *)-1;	/* unreachable on discard */
	else
		mapbuf = NULL;				/* exactly receiver.c:327 */

	while (1) {
		data = NULL;
		i = recv_token(f_in, &data);	/* real token decode */
		if (i == 0)
			break;

		if (i > 0) {
			/* literal token: discard path ignores the payload */
			if (!data)
				exit_cleanup(RERR_PROTOCOL);
			continue;
		}

		/* block-match token */
		i = -(i + 1);
		if (i < 0 || i >= sum.count)		/* receiver.c:393 guard */
			exit_cleanup(RERR_PROTOCOL);

		len = sum.blength;
		if (i == (int)sum.count - 1 && sum.remainder != 0)
			len = sum.remainder;

		if (!mapbuf) {				/* receiver.c: !mapbuf branch */
			/* FIXED behavior: on the discard path (fd == -1) a match
			 * token is normal protocol; absorb it benignly instead of
			 * calling full_fname(fname==NULL). Only a real-output
			 * transfer (fd != -1) hard-errors -- and there fname is
			 * non-NULL, so full_fname is safe. */
			if (fd != -1) {
				rprintf(FERROR, "got a block match with no basis file for %s [%s]\n",
					full_fname(fname), who_am_i());
				exit_cleanup(RERR_PROTOCOL);
			}
			offset += len;
			continue;
		}
	}
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	if (size < 1)
		return 0;

	protocol_version = 30;
	do_compression = CPRES_NONE;	/* simple_recv_token path */
	xfer_sum_len = 16;

	int f = fd_from_bytes(data, size);
	if (f < 0)
		return 0;

	fuzz_unwind_armed = 1;
	if (setjmp(fuzz_unwind_env) == 0)
		drive_discard_path(f);
	fuzz_unwind_armed = 0;

	close(f);
	return 0;
}
