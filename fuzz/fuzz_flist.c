/*
 * fuzz_flist.c - STAGED STUB (not yet wired into fuzz/Makefile).
 *
 * Intended target: recv_file_entry() (flist.c ~682-1169) - the sender->receiver
 * file-list entry stream, with the stateful lastname/thisname reconstruction
 * (reference.md Part 3.2) and the symlink-target path (line ~1131/1164). Guards
 * of interest: the l2 >= MAXPATHLEN - l1 overflow check (line 724) and the
 * linkname_len bounds (line 941).
 *
 * WHY THIS IS A STUB (honest blocker, not laziness):
 *   recv_file_entry is NOT self-contained the way io.c's primitives are. The
 *   flist.o object has ~167 undefined references that recv_file_entry's call
 *   graph actually reaches, spanning subsystems we would have to either link
 *   (dragging un-instrumented complexity + their own transitive deps) or stub
 *   so extensively that we risk masking the very bugs we want to find:
 *     - uid/gid mapping            (add_uid, add_gid, uid_to_user, ...)
 *     - path utilities             (clean_fname, sanitize_path,
 *                                   count_dir_elements, push/pop_local_filters)
 *     - filtering                  (check_filter, name_is_excluded, filter_list)
 *     - checksums                  (file_checksum, csum_len_for_type)
 *     - acls / xattrs              (get_acl, get_xattr, ...)
 *     - the flist pool allocator + flist_expand
 *   Doing this hygienically is a workstream of its own. Within the WS2 budget,
 *   io.c (proven) and token.c (CPRES_NONE path, proven) were prioritized.
 *
 * REQUIRED GLOBAL INIT when this is completed (reference.md Part 3.5):
 *   protocol_version (>=30 for varint30 widths), preserve_links/devices/specials,
 *   sender_symlink_iconv = NULL, munge_symlinks = 0, sanitize_paths = 0,
 *   uid_ndx/gid_ndx/acls_ndx/xattrs_ndx (drive which optional fields are read),
 *   file_extra_cnt + the *_extra index globals (control F_* slot layout), and a
 *   real struct file_list with an alloc pool (flist->files / flist->pool).
 *   Crucially, recv_file_entry is STATEFUL: its static lastname[] persists, so a
 *   useful harness must feed a SEQUENCE of entries (with/without XMIT_SAME_NAME)
 *   per input - reset/re-init the statics between inputs is not possible from
 *   outside, so accept the documented cross-input coupling as fuzz_token does.
 *
 * Strategy to finish (sketch): link flist.o + io.o + util1.o/util2.o + uidlist.o
 *   + exclude.o + hashtable.o + the flist pool, stub only the leaf I/O syscalls
 *   and logging, and assert ASan stays the oracle (guards => longjmp unwind).
 */

#include <stdint.h>
#include <stddef.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	(void)data; (void)size;
	return 0;	/* no-op until the link surface above is built out */
}
