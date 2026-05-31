/*
 * recv_discard_stubs.c - extra link-time symbols for fuzz_recv_discard.
 *
 * fuzz_recv_discard links the REAL util1.o (for full_fname) on top of the
 * shared stubs.c. util1.o is a whole translation unit, so it references many
 * symbols from other rsync TUs that are NEVER on the discard-path / full_fname
 * code path we exercise. We supply them here so the object links.
 *
 * IMPORTANT: none of these are reached by the harness. full_fname(NULL) reads
 * *fn (util1.c:1282) BEFORE touching module_id/lp_name/curr_dir, so the NULL
 * deref fires first. The function stubs abort() if ever called, which would
 * surface as an obvious failure rather than a silent wrong answer (oracle
 * fidelity, same spirit as stubs.c).
 *
 * The globals are given the same neutral defaults rsync uses at start-up:
 *   module_id   = -1  (already in stubs.c)  -> full_fname skips lp_name()
 *   module_dirlen = 0                       -> full_fname's curr_dir math is inert
 * so full_fname is deterministic up to the (crashing) *fn read.
 */

#include "rsync.h"
#include <stdlib.h>

/* ---- globals util1.o references (neutral start-up values) ---- */
unsigned int module_dirlen = 0;
char *module_dir = NULL;
char *partial_dir = NULL;
filter_rule_list daemon_filter_list = { .debug_type = " [daemon]" };

int am_daemon = 0;
int am_chrooted = 0;
int dry_run = 0;
int relative_paths = 0;
int modify_window = 0;
int preserve_xattrs = 0;
int preallocate_files = 0;
int omit_link_times = 0;

/* ---- functions util1.o references but the full_fname path never calls ---- */
#define NEVER(name) do { (void)(name); abort(); } while (0)

char *lp_name(int module_id_) { (void)module_id_; NEVER("lp_name"); return NULL; }
int check_filter(filter_rule_list *lp, enum logcode code, const char *name, int name_is_dir)
{ (void)lp; (void)code; (void)name; (void)name_is_dir; NEVER("check_filter"); return 0; }
int wildmatch(const char *p, const char *t) { (void)p; (void)t; NEVER("wildmatch"); return 0; }
int copy_xattrs(const char *s, const char *d) { (void)s; (void)d; NEVER("copy_xattrs"); return 0; }
int secure_relative_open(const char *b, const char *r, int fl, mode_t m)
{ (void)b; (void)r; (void)fl; (void)m; NEVER("secure_relative_open"); return -1; }

OFF_T do_fallocate(int fd, OFF_T off, OFF_T len) { (void)fd; (void)off; (void)len; NEVER("do_fallocate"); return -1; }
int do_fstat(int fd, STRUCT_STAT *st) { (void)fd; (void)st; NEVER("do_fstat"); return -1; }
int do_fsync(int fd) { (void)fd; NEVER("do_fsync"); return -1; }
int do_ftruncate(int fd, OFF_T sz) { (void)fd; (void)sz; NEVER("do_ftruncate"); return -1; }
int do_stat(const char *p, STRUCT_STAT *st) { (void)p; (void)st; NEVER("do_stat"); return -1; }
int do_lstat_at(const char *p, STRUCT_STAT *st) { (void)p; (void)st; NEVER("do_lstat_at"); return -1; }
int do_mkdir(char *p, mode_t m) { (void)p; (void)m; NEVER("do_mkdir"); return -1; }
int do_mkdir_at(char *p, mode_t m) { (void)p; (void)m; NEVER("do_mkdir_at"); return -1; }
int do_rmdir_at(const char *p) { (void)p; NEVER("do_rmdir_at"); return -1; }
int do_unlink_at(const char *p) { (void)p; NEVER("do_unlink_at"); return -1; }
int do_rename_at(const char *o, const char *n) { (void)o; (void)n; NEVER("do_rename_at"); return -1; }
int do_open_at(const char *p, int fl, mode_t m) { (void)p; (void)fl; (void)m; NEVER("do_open_at"); return -1; }
int do_open_nofollow(const char *p, int fl) { (void)p; (void)fl; NEVER("do_open_nofollow"); return -1; }
int do_utimensat_at(const char *p, STRUCT_STAT *st) { (void)p; (void)st; NEVER("do_utimensat_at"); return -1; }
int do_lutimes(const char *p, STRUCT_STAT *st) { (void)p; (void)st; NEVER("do_lutimes"); return -1; }
int do_utimes(const char *p, STRUCT_STAT *st) { (void)p; (void)st; NEVER("do_utimes"); return -1; }
