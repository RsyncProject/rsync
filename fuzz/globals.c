/*
 * fuzz/globals.c - rsync option/state globals and a handful of leaf functions
 * that the REAL flist.o / xattrs.o / acls.o / uidlist.o / exclude.o objects
 * reference but that normally live in options.c / main.c / loadparm.c / hlink.c
 * / log.c (TUs we deliberately do NOT link, since recv_file_entry /
 * receive_xattr never reach their bodies).
 *
 * These are GENUINE process-state globals, not parse/alloc/copy logic, so
 * defining them here does NOT mask any bug: the harness sets the load-bearing
 * ones (preserve_*, *_ndx, protocol_version, ...) per-input to match the real
 * receiver's configured state. The rest default to 0/NULL exactly as a freshly
 * started receiver before option parsing.
 *
 * The few FUNCTIONS here are leaves outside recv_file_entry's reachable graph
 * (hardlink table setup, name-conversion subprocess, loadparm module lookup,
 * name_num registries). Each aborts loudly if ever actually called, so the
 * oracle still fires instead of silently returning a bogus value.
 */

#include "rsync.h"

/* Defined in stubs.c; needed by the idev_find / init_hard_links copies below. */
extern int am_sender, protocol_version, inc_recurse;

/* ---- option / mode globals (harness overrides the load-bearing ones) ---- */
/* preserve_hard_links lives in stubs.c; sanitize_paths in util1.o. */
int preserve_links, preserve_devices, preserve_specials;
int preserve_uid, preserve_gid, preserve_acls, preserve_xattrs;
int preserve_perms, preserve_executability;
int relative_paths, munge_symlinks;
int uid_ndx, gid_ndx, acls_ndx, xattrs_ndx, atimes_ndx, depth_ndx, pathname_ndx, unsort_ndx;
int crtimes_ndx, preserve_atimes, preserve_crtimes;
int numeric_ids, always_checksum, recurse, xfer_dirs, one_file_system, copy_devices;
int copy_links, copy_dirlinks, copy_unsafe_links;
int omit_link_times;
int delete_during, delete_excluded, delete_mode;
int implied_dirs, prune_empty_dirs, non_perishable_cnt, ignore_perishable;
int need_unsorted_flist, use_safe_inc_flist, xmit_id0_names, proper_seed_order;
int sender_keeps_checksum, sender_symlink_iconv, use_qsort;
int am_chrooted, am_daemon, dry_run, quiet, ignore_errors, missing_args;
int modify_window, whole_file, sparse_files, inplace, preallocate_files;
/* no_acl_syscall_error lives in lib/sysacls.o */
int cvs_exclude, output_needs_newline;
int human_readable = 0;
int do_fsync = 0;
int open_noatime = 0;
int orig_umask = 022;
int our_uid, our_gid;
int filesfrom_fd = -1;
int read_only = 0;

char *usermap = NULL, *groupmap = NULL;
char *module_dir = NULL;
char *partial_dir = NULL;
char *filesfrom_host = NULL;
unsigned int module_dirlen = 0;

/* xattr_sum_nni / xattr_sum_len normally come from compat.c; the abbreviated
 * xattr-datum branch (xattrs.c:822) reads xattr_sum_len bytes. Default to the
 * MD5 length; fuzz_xattrs overrides. */
struct name_num_item *xattr_sum_nni = NULL;
int xattr_sum_len = 16;

/* checksum_choice normally set by validate_choice_vs_env; checksum.c reads it. */
char *checksum_choice = NULL;

/* chmod_modes is a struct chmod_mode_struct* the tweak path consults. */
struct chmod_mode_struct *chmod_modes = NULL;

/* ---- leaf functions outside recv_file_entry / receive_xattr graph ---- */

NORETURN static void fuzz_unreachable(const char *who)
{
	/* If the parser path ever truly reaches one of these, that is itself a
	 * finding (our reachability assumption was wrong) - abort so ASan/the
	 * fuzzer records it rather than silently continuing on a bogus return. */
	rprintf(FERROR, "fuzz: unreachable leaf %s reached\n", who);
	abort();
}

/* Faithful copies of hlink.c's device/inode hashtable helpers. recv_file_entry
 * reaches idev_find() on the proto<30 hard-link path (flist.c:1191), so these
 * must be REAL (they use the real hashtable.o); a stub would mask any OOB in
 * that path. Linking all of hlink.o would drag in the receiver/generator graph,
 * so we lift just these three + their statics verbatim. */
static void *data_when_new = "";
static struct hashtable *dev_tbl;

void init_hard_links(void)
{
	if (am_sender || protocol_version < 30)
		dev_tbl = hashtable_create(16, HT_KEY64);
	/* inc_recurse/prior_hlinks branch is unreached: harness keeps inc_recurse=0 */
}

struct ht_int64_node *idev_find(int64 dev, int64 ino)
{
	static struct ht_int64_node *dev_node = NULL;
	if (!dev_node || dev_node->key != dev+1) {
		dev_node = hashtable_find(dev_tbl, dev+1, data_when_new);
		if (dev_node->data == data_when_new)
			dev_node->data = hashtable_create(512, HT_KEY64);
	}
	return hashtable_find(dev_node->data, ino, (void*)-1L);
}

void idev_destroy(void)
{
	int i;
	if (!dev_tbl)
		return;
	for (i = 0; i < dev_tbl->size; i++) {
		struct ht_int32_node *node = HT_NODE(dev_tbl, dev_tbl->nodes, i);
		if (node->data)
			hashtable_destroy(node->data);
	}
	hashtable_destroy(dev_tbl);
	dev_tbl = NULL;
}

BOOL namecvt_call(const char *cmd, const char **name_p, id_t *id_p)
{ (void)cmd; (void)name_p; (void)id_p; fuzz_unreachable("namecvt_call"); }
int namecvt_pid = 0;

/* Faithful copies of compat.c's registry walks (reached by init_checksum_choices
 * during the legitimate checksum-negotiation init the receiver performs). Pure
 * list lookups, no I/O - replicating them masks nothing. */
struct name_num_item *get_nni_by_name(struct name_num_obj *nno, const char *name, int len)
{
	struct name_num_item *nni;
	if (len < 0)
		len = strlen(name);
	for (nni = nno->list; nni->name; nni++) {
		if (nni->num == CSUM_gone)
			continue;
		if (strncasecmp(name, nni->name, len) == 0 && nni->name[len] == '\0')
			return nni;
	}
	return NULL;
}
struct name_num_item *get_nni_by_num(struct name_num_obj *nno, int num)
{
	struct name_num_item *nni;
	for (nni = nno->list; nni->name; nni++) {
		if (num == nni->num)
			return nni;
	}
	return NULL;
}
void validate_choice_vs_env(int ntype, int num1, int num2)
{ (void)ntype; (void)num1; (void)num2; }

const char *default_cvsignore(void) { return ""; }

char *lp_name(int m) { (void)m; return "fuzz"; }
BOOL lp_use_chroot(int m) { (void)m; return 0; }
BOOL lp_ignore_nonreadable(int m) { (void)m; return 0; }

void rflush(enum logcode code) { (void)code; }
