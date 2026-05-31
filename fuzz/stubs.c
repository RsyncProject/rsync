/*
 * fuzz/stubs.c - minimal external symbols required to link rsync wire-parser
 * object files (io.o, token.o, ...) into a standalone libFuzzer harness.
 *
 * Strategy (see fuzz/README.md "Linking / stubbing strategy"):
 *   - The object under test (io.o etc.) is compiled UNMODIFIED from rsync's
 *     own sources with the campaign sanitizer CFLAGS.
 *   - Everything io.o references that we do NOT want to drag in (logging,
 *     cleanup, the rest of rsync's translation units) is supplied here.
 *   - The single most important stub is _exit_cleanup(): rsync's wire-range
 *     guards call exit_cleanup(RERR_*) on a malformed/over-range value. In the
 *     real program that terminates the process; in the fuzzer we longjmp back
 *     to the harness so a *correctly rejected* hostile input is NOT counted as
 *     a crash. A genuine memory bug still trips ASan/UBSan BEFORE any guard
 *     fires, so this preserves oracle fidelity: guard-hit => clean unwind,
 *     real OOB => sanitizer abort.
 *
 * No rsync source file is modified by this workstream; all shims live here.
 */

#include "rsync.h"
#include <setjmp.h>

/* Harness sets this up; exit_cleanup / out_of_memory / overflow_exit unwind to it. */
jmp_buf fuzz_unwind_env;
int fuzz_unwind_armed;

/* ------- functions io.o (and friends) call that we shim ------- */

NORETURN void _exit_cleanup(int code, const char *file, int line)
{
	(void)code; (void)file; (void)line;
	if (fuzz_unwind_armed)
		longjmp(fuzz_unwind_env, 1);
	/* Not armed: abort loudly rather than silently mis-behaving. */
	_exit(99);
}

__attribute__((weak)) NORETURN void _out_of_memory(const char *msg, const char *file, int line)
{
	(void)msg; (void)file; (void)line;
	if (fuzz_unwind_armed)
		longjmp(fuzz_unwind_env, 2);
	_exit(99);
}

__attribute__((weak)) NORETURN void _overflow_exit(const char *msg, const char *file, int line)
{
	(void)msg; (void)file; (void)line;
	if (fuzz_unwind_armed)
		longjmp(fuzz_unwind_env, 3);
	_exit(99);
}

void rprintf(enum logcode code, const char *format, ...) { (void)code; (void)format; }
void rsyserr(enum logcode code, int errcode, const char *format, ...)
{ (void)code; (void)errcode; (void)format; }
void rwrite(enum logcode code, const char *buf, int len, int is_utf8)
{ (void)code; (void)buf; (void)len; (void)is_utf8; }

const char *who_am_i(void) { return "fuzz"; }

__attribute__((weak)) char *do_big_num(int64 num, int human_flag, const char *fract)
{
	static char buf[32];
	(void)human_flag; (void)fract;
	snprintf(buf, sizeof buf, "%lld", (long long)num);
	return buf;
}

__attribute__((weak)) int msleep(int t) { (void)t; return 0; }

/* my_alloc: a self-contained allocator so ASan tracks every wire-driven
 * allocation. Mirrors rsync's semantics closely enough for the parsers:
 * honours max_alloc, returns NULL when file==NULL on over-limit (callers like
 * EXPAND_ITEM_LIST rely on that), zero-fills on the calloc sentinel. */
/* WEAK: real util2.o defines do_calloc + my_alloc; when fuzz_flist/fuzz_xattrs
 * link util2.o those strong defs win. fuzz_io/fuzz_token (no util2.o) fall back
 * to these. Same weakening applies to the few globals flist.o itself defines. */
__attribute__((weak)) char *do_calloc = "42";
extern size_t max_alloc;

__attribute__((weak)) void *my_alloc(void *ptr, size_t num, size_t size, const char *file, int line)
{
	(void)line;
	if (size && num >= max_alloc / size) {
		if (!file)
			return NULL;
		_exit_cleanup(RERR_MALLOC, file, line);
	}
	if (!ptr || ptr == do_calloc)
		return calloc(num ? num : 1, size ? size : 1);
	return realloc(ptr, num * size);
}

/* ------- global state io.o references; defaults are fine for the parsers ------- */

struct stats stats;
size_t max_alloc = 1u << 30;	/* 1 GiB cap so over-range counts still get rejected by guards */

int protocol_version = PROTOCOL_VERSION;
__attribute__((weak)) int xfer_sum_len = 16;	/* MD5-ish default; flist/checksum may override */
int file_extra_cnt = 0;

int am_server = 0, am_sender = 0, am_generator = 0, am_receiver = 0, am_root = 0;
int local_server = 0, daemon_connection = 0;
int inc_recurse = 0;
__attribute__((weak)) int io_error = 0;	/* flist.o defines this strong */
int io_timeout = 0;
int batch_fd = -1;
int eol_nulls = 0;
int read_batch = 0;
int list_only = 0;
int protect_args = 0;
int checksum_seed = 0;
__attribute__((weak)) int flist_eof = 0;	/* flist.o strong */
int compat_flags = 0;
__attribute__((weak)) int file_total = 0;	/* flist.o strong */
__attribute__((weak)) int file_old_total = 0;	/* flist.o strong */
int preserve_hard_links = 0;
int remove_source_files = 0;
int extra_flist_sending_enabled = 0;
int msgs2stderr = 0;
int flush_ok_after_signal = 0;
int bwlimit = 0;
size_t bwlimit_writemax = 0;
int stop_at_utime = 0;

/* INFO_GTE / DEBUG_GTE index these directly, so they must be real zero arrays
 * (all log verbosity off => parser hot path, no rprintf side effects). */
short info_levels[COUNT_INFO];
short debug_levels[COUNT_DEBUG];

__attribute__((weak)) struct file_list *cur_flist = NULL;	/* flist.o strong */

/* ------- functions io.o references but the parser paths never reach ------- */

void check_for_finished_files(int itemizing, enum logcode code, int check_redo)
{ (void)itemizing; (void)code; (void)check_redo; }

/* flist_for_ndx lives in rsync.c, which NO harness links, so this stub is the
 * only definition. The reachable receive-side parser paths exercised here never
 * call it (recv_file_entry's proto<30 hardlink path uses idev_find, not
 * flist_for_ndx). A NULL return would silently diverge from real receiver
 * behavior and could mask a bug, so instead of returning fake data we abort
 * loudly: if a future parser path ever reaches it, the harness fails the run
 * rather than carrying on with wrong state. (Not made weak: there is no real
 * flist_for_ndx object to override it; weak would just leave NULL behind.) */
struct file_list *flist_for_ndx(int ndx, const char *fatal_error_msg)
{
	fprintf(stderr, "fuzz/stubs.c: flist_for_ndx(%d, %s) reached -- the "
		"harness does not link the real implementation; aborting rather "
		"than returning fake flist data.\n",
		ndx, fatal_error_msg ? fatal_error_msg : "(null)");
	abort();
}

__attribute__((weak)) struct file_list *recv_file_list(int f, int dir_ndx) { (void)f; (void)dir_ndx; return NULL; }
__attribute__((weak)) void send_extra_file_list(int f, int at_least) { (void)f; (void)at_least; }

__attribute__((weak)) int flist_ndx_pop(flist_ndx_list *lp) { (void)lp; return -1; }
__attribute__((weak)) void flist_ndx_push(flist_ndx_list *lp, int ndx) { (void)lp; (void)ndx; }

void log_delete(const char *fname, int mode) { (void)fname; (void)mode; }
void match_hard_links(struct file_list *flist) { (void)flist; }
void successful_send(int ndx) { (void)ndx; }
__attribute__((weak)) int glob_expand(const char *arg, char ***argv_p, int *argc_p, int *maxargs_p)
{ (void)arg; (void)argv_p; (void)argc_p; (void)maxargs_p; return 0; }
__attribute__((weak)) void glob_expand_module(char *base1, char *arg, char ***argv_p, int *argc_p, int *maxargs_p)
{ (void)base1; (void)arg; (void)argv_p; (void)argc_p; (void)maxargs_p; }

__attribute__((weak)) void add_implied_include(const char *arg, int skip_daemon_module) { (void)arg; (void)skip_daemon_module; }
__attribute__((weak)) void free_implied_include_partial_string(void) {}
__attribute__((weak)) void implied_include_partial_string(const char *s_start, const char *s_end) { (void)s_start; (void)s_end; }

int iconvbufs(iconv_t ic, xbuf *in, xbuf *out, int flags)
{ (void)ic; (void)in; (void)out; (void)flags; return 0; }
iconv_t ic_send = (iconv_t)-1;
iconv_t ic_recv = (iconv_t)-1;

char *filesfrom_convert = NULL;

/* ------- token.c (compression) globals/shims ------- */
/* do_compression is set per-input by fuzz_token; CPRES_NONE => simple path. */
int do_compression = 0;
int do_compression_level = 0;
int do_compression_threads = 0;
int module_id = -1;
char *skip_compress = NULL;

char *lp_dont_compress(int module_id_) { (void)module_id_; return NULL; }
__attribute__((weak)) char *map_ptr(struct map_struct *map, OFF_T offset, int32 len)
{ (void)map; (void)offset; (void)len; return NULL; }
