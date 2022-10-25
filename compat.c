/*
 * Compatibility routines for older rsync protocol versions.
 *
 * Copyright (C) Andrew Tridgell 1996
 * Copyright (C) Paul Mackerras 1996
 * Copyright (C) 2004-2022 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"
#include "itypes.h"
#include "ifuncs.h"

extern int am_server;
extern int am_sender;
extern int local_server;
extern int inplace;
extern int recurse;
extern int use_qsort;
extern int allow_inc_recurse;
extern int preallocate_files;
extern int append_mode;
extern int fuzzy_basis;
extern int read_batch;
extern int write_batch;
extern int delay_updates;
extern int checksum_seed;
extern int basis_dir_cnt;
extern int prune_empty_dirs;
extern int protocol_version;
extern int protect_args;
extern int preserve_uid;
extern int preserve_gid;
extern int preserve_atimes;
extern int preserve_crtimes;
extern int preserve_acls;
extern int preserve_xattrs;
extern int xfer_flags_as_varint;
extern int need_messages_from_generator;
extern int delete_mode, delete_before, delete_during, delete_after;
extern int do_compression;
extern int do_compression_level;
extern int saw_stderr_opt;
extern int msgs2stderr;
extern char *shell_cmd;
extern char *partial_dir;
extern char *files_from;
extern char *filesfrom_host;
extern const char *checksum_choice;
extern const char *compress_choice;
extern char *daemon_auth_choices;
extern filter_rule_list filter_list;
extern int need_unsorted_flist;
#ifdef ICONV_OPTION
extern iconv_t ic_send, ic_recv;
extern char *iconv_opt;
#endif
extern struct name_num_obj valid_checksums, valid_auth_checksums;

extern struct name_num_item *xfer_sum_nni;

int remote_protocol = 0;
int file_extra_cnt = 0; /* count of file-list extras that everyone gets */
int inc_recurse = 0;
int compat_flags = 0;
int use_safe_inc_flist = 0;
int want_xattr_optim = 0;
int proper_seed_order = 0;
int inplace_partial = 0;
int do_negotiated_strings = 0;
int xmit_id0_names = 0;

struct name_num_item *xattr_sum_nni;
int xattr_sum_len = 0;

/* These index values are for the file-list's extra-attribute array. */
int pathname_ndx, depth_ndx, atimes_ndx, crtimes_ndx, uid_ndx, gid_ndx, acls_ndx, xattrs_ndx, unsort_ndx;

int receiver_symlink_times = 0; /* receiver can set the time on a symlink */
int sender_symlink_iconv = 0;	/* sender should convert symlink content */

#ifdef ICONV_OPTION
int filesfrom_convert = 0;
#endif

#define MAX_NSTR_STRLEN 256

struct name_num_item valid_compressions_items[] = {
#ifdef SUPPORT_ZSTD
	{ CPRES_ZSTD, 0, "zstd", NULL },
#endif
#ifdef SUPPORT_LZ4
	{ CPRES_LZ4, 0, "lz4", NULL },
#endif
	{ CPRES_ZLIBX, 0, "zlibx", NULL },
	{ CPRES_ZLIB, 0, "zlib", NULL },
	{ CPRES_NONE, 0, "none", NULL },
	{ 0, 0, NULL, NULL }
};

struct name_num_obj valid_compressions = {
	"compress", NULL, 0, 0, valid_compressions_items
};

#define CF_INC_RECURSE	 (1<<0)
#define CF_SYMLINK_TIMES (1<<1)
#define CF_SYMLINK_ICONV (1<<2)
#define CF_SAFE_FLIST	 (1<<3)
#define CF_AVOID_XATTR_OPTIM (1<<4)
#define CF_CHKSUM_SEED_FIX (1<<5)
#define CF_INPLACE_PARTIAL_DIR (1<<6)
#define CF_VARINT_FLIST_FLAGS (1<<7)
#define CF_ID0_NAMES (1<<8)

static const char *client_info;

/* The server makes sure that if either side only supports a pre-release
 * version of a protocol, that both sides must speak a compatible version
 * of that protocol for it to be advertised as available. */
static void check_sub_protocol(void)
{
	char *dot;
	int their_protocol, their_sub;
	int our_sub = get_subprotocol_version();

	/* client_info starts with VER.SUB string if client is a pre-release. */
	if (!(their_protocol = atoi(client_info))
	 || !(dot = strchr(client_info, '.'))
	 || !(their_sub = atoi(dot+1))) {
#if SUBPROTOCOL_VERSION != 0
		if (our_sub)
			protocol_version--;
#endif
		return;
	}

	if (their_protocol < protocol_version) {
		if (their_sub)
			protocol_version = their_protocol - 1;
		return;
	}

	if (their_protocol > protocol_version)
		their_sub = 0; /* 0 == final version of older protocol */
	if (their_sub != our_sub)
		protocol_version--;
}

void set_allow_inc_recurse(void)
{
	if (!local_server)
		client_info = shell_cmd ? shell_cmd : "";
	else if (am_server) {
		char buf[64];
		maybe_add_e_option(buf, sizeof buf);
		client_info = *buf ? strdup(buf+1) : ""; /* The +1 skips the leading "e". */
	}

	if (!recurse || use_qsort)
		allow_inc_recurse = 0;
	else if (!am_sender
	 && (delete_before || delete_after
	  || delay_updates || prune_empty_dirs))
		allow_inc_recurse = 0;
	else if (am_server && strchr(client_info, 'i') == NULL)
		allow_inc_recurse = 0;
}

void parse_compress_choice(int final_call)
{
	if (valid_compressions.negotiated_nni)
		do_compression = valid_compressions.negotiated_nni->num;
	else if (compress_choice) {
		struct name_num_item *nni = get_nni_by_name(&valid_compressions, compress_choice, -1);
		if (!nni) {
			rprintf(FERROR, "unknown compress name: %s\n", compress_choice);
			exit_cleanup(RERR_UNSUPPORTED);
		}
		do_compression = nni->num;
		if (am_server)
			validate_choice_vs_env(NSTR_COMPRESS, do_compression, -1);
	} else if (do_compression)
		do_compression = CPRES_ZLIB;
	else
		do_compression = CPRES_NONE;

	if (do_compression != CPRES_NONE && final_call)
		init_compression_level(); /* There's a chance this might turn compression off! */

	if (do_compression == CPRES_NONE)
		compress_choice = NULL;

	/* Snag the compression name for both write_batch's option output & the following debug output. */
	if (valid_compressions.negotiated_nni)
		compress_choice = valid_compressions.negotiated_nni->name;
	else if (compress_choice == NULL) {
		struct name_num_item *nni = get_nni_by_num(&valid_compressions, do_compression);
		compress_choice = nni ? nni->name : "UNKNOWN";
	}

	if (final_call && DEBUG_GTE(NSTR, am_server ? 3 : 1)
	 && (do_compression != CPRES_NONE || do_compression_level != CLVL_NOT_SPECIFIED)) {
		rprintf(FINFO, "%s%s compress: %s (level %d)\n",
			am_server ? "Server" : "Client",
			valid_compressions.negotiated_nni ? " negotiated" : "",
			compress_choice, do_compression_level);
	}
}

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

static void init_nno_saw(struct name_num_obj *nno, int val)
{
	struct name_num_item *nni;
	int cnt;

	if (!nno->saw_len) {
		for (nni = nno->list; nni->name; nni++) {
			if (nni->num >= nno->saw_len)
				nno->saw_len = nni->num + 1;
		}
	}

	if (!nno->saw) {
		nno->saw = new_array0(uchar, nno->saw_len);

		/* We'll take this opportunity to set the main_nni values for duplicates. */
		for (cnt = 1, nni = nno->list; nni->name; nni++, cnt++) {
			if (nni->num == CSUM_gone)
				continue;
			if (nno->saw[nni->num])
				nni->main_nni = &nno->list[nno->saw[nni->num]-1];
			else
				nno->saw[nni->num] = cnt;
		}
	}

	memset(nno->saw, val, nno->saw_len);
}

/* Simplify the user-provided string so that it contains valid names without any duplicates.
 * It also sets the "saw" flags to a 1-relative count of which name was seen first. */
static int parse_nni_str(struct name_num_obj *nno, const char *from, char *tobuf, int tobuf_len)
{
	char *to = tobuf, *tok = NULL;
	int saw_tok = 0, cnt = 0;

	while (1) {
		int at_space = isSpace(from);
		char ch = *from++;
		if (ch == '&')
			ch = '\0';
		if (!ch || at_space) {
			if (tok) {
				struct name_num_item *nni = get_nni_by_name(nno, tok, to - tok);
				if (nni && !nno->saw[nni->num]) {
					nno->saw[nni->num] = ++cnt;
					if (nni->main_nni) {
						to = tok + strlcpy(tok, nni->main_nni->name, tobuf_len - (tok - tobuf));
						if (to - tobuf >= tobuf_len) {
							to = tok - 1;
							break;
						}
					}
				} else
					to = tok - (tok != tobuf);
				saw_tok = 1;
				tok = NULL;
			}
			if (!ch)
				break;
			continue;
		}
		if (!tok) {
			if (to != tobuf)
				*to++ = ' ';
			tok = to;
		}
		if (to - tobuf >= tobuf_len - 1) {
			to = tok - (tok != tobuf);
			break;
		}
		*to++ = ch;
	}
	*to = '\0';

	if (saw_tok && to == tobuf)
		return strlcpy(tobuf, "INVALID", MAX_NSTR_STRLEN);

	return to - tobuf;
}

static int parse_negotiate_str(struct name_num_obj *nno, char *tmpbuf)
{
	struct name_num_item *nni, *ret = NULL;
	int best = nno->saw_len; /* We want best == 1 from the client list, so start with a big number. */
	char *space, *tok = tmpbuf;
	while (tok) {
		while (*tok == ' ') tok++; /* Should be unneeded... */
		if (!*tok)
			break;
		if ((space = strchr(tok, ' ')) != NULL)
			*space = '\0';
		nni = get_nni_by_name(nno, tok, -1);
		if (space) {
			*space = ' ';
			tok = space + 1;
		} else
			tok = NULL;
		if (!nni || !nno->saw[nni->num] || best <= nno->saw[nni->num])
			continue;
		ret = nni;
		best = nno->saw[nni->num];
		if (best == 1 || am_server) /* The server side stops at the first acceptable client choice */
			break;
	}
	if (ret) {
		free(nno->saw);
		nno->saw = NULL;
		nno->negotiated_nni = ret->main_nni ? ret->main_nni : ret;
		return 1;
	}
	return 0;
}

/* This routine is always called with a tmpbuf of MAX_NSTR_STRLEN length, but the
 * buffer may be pre-populated with a "len" length string to use OR a len of -1
 * to tell us to read a string from the fd. */
static void recv_negotiate_str(int f_in, struct name_num_obj *nno, char *tmpbuf, int len)
{
	if (len < 0)
		len = read_vstring(f_in, tmpbuf, MAX_NSTR_STRLEN);

	if (DEBUG_GTE(NSTR, am_server ? 3 : 2)) {
		if (am_server)
			rprintf(FINFO, "Client %s list (on server): %s\n", nno->type, tmpbuf);
		else
			rprintf(FINFO, "Server %s list (on client): %s\n", nno->type, tmpbuf);
	}

	if (len > 0 && parse_negotiate_str(nno, tmpbuf))
		return;

	if (!am_server || !do_negotiated_strings) {
		char *cp = tmpbuf;
		int j;
		rprintf(FERROR, "Failed to negotiate a %s choice.\n", nno->type);
		rprintf(FERROR, "%s list: %s\n", am_server ? "Client" : "Server", tmpbuf);
		/* Recreate our original list from the saw values. This can't overflow our huge
		 * buffer because we don't have enough valid entries to get anywhere close. */
		for (j = 1, *cp = '\0'; j <= nno->saw_len; j++) {
			struct name_num_item *nni;
			for (nni = nno->list; nni->name; nni++) {
				if (nno->saw[nni->num] == j) {
					*cp++ = ' ';
					cp += strlcpy(cp, nni->name, MAX_NSTR_STRLEN - (cp - tmpbuf));
					break;
				}
			}
		}
		if (!*tmpbuf)
			strlcpy(cp, " INVALID", MAX_NSTR_STRLEN);
		rprintf(FERROR, "%s list:%s\n", am_server ? "Server" : "Client", tmpbuf);
	}

	exit_cleanup(RERR_UNSUPPORTED);
}

static const char *getenv_nstr(int ntype)
{
	const char *env_str = getenv(ntype == NSTR_COMPRESS ? "RSYNC_COMPRESS_LIST" : "RSYNC_CHECKSUM_LIST");

	/* When writing a batch file, we always negotiate an old-style choice. */
	if (write_batch)
		env_str = ntype == NSTR_COMPRESS ? "zlib" : protocol_version >= 30 ? "md5" : "md4";

	if (am_server && env_str) {
		char *cp = strchr(env_str, '&');
		if (cp)
			env_str = cp + 1;
	}

	return env_str;
}

void validate_choice_vs_env(int ntype, int num1, int num2)
{
	struct name_num_obj *nno = ntype == NSTR_COMPRESS ? &valid_compressions : &valid_checksums;
	const char *list_str = getenv_nstr(ntype);
	char tmpbuf[MAX_NSTR_STRLEN];

	if (!list_str)
		return;

	while (isSpace(list_str)) list_str++;

	if (!*list_str)
		return;

	init_nno_saw(nno, 0);
	parse_nni_str(nno, list_str, tmpbuf, MAX_NSTR_STRLEN);

	if (ntype == NSTR_CHECKSUM) /* If "md4" is in the env list, all the old MD4 choices are OK too. */
		nno->saw[CSUM_MD4_ARCHAIC] = nno->saw[CSUM_MD4_BUSTED] = nno->saw[CSUM_MD4_OLD] = nno->saw[CSUM_MD4];

	if (!nno->saw[num1] || (num2 >= 0 && !nno->saw[num2])) {
		rprintf(FERROR, "Your --%s-choice value (%s) was refused by the server.\n",
			ntype == NSTR_COMPRESS ? "compress" : "checksum",
			ntype == NSTR_COMPRESS ? compress_choice : checksum_choice);
		exit_cleanup(RERR_UNSUPPORTED);
	}

	free(nno->saw);
	nno->saw = NULL;
}

/* The saw buffer is initialized and used to store ordinal values from 1 to N
 * for the order of the args in the array.  If dup_markup == '\0', duplicates
 * are removed otherwise the char is prefixed to the duplicate term and, if it
 * is an opening paren/bracket/brace, the matching closing char is suffixed.
 * "none" is removed on the client side unless dup_markup != '\0'. */
int get_default_nno_list(struct name_num_obj *nno, char *to_buf, int to_buf_len, char dup_markup)
{
	struct name_num_item *nni;
	int len = 0, cnt = 0;
	char delim = '\0', post_delim;

	switch (dup_markup) {
	case '(': post_delim = ')'; break;
	case '[': post_delim = ']'; break;
	case '{': post_delim = '}'; break;
	default: post_delim = '\0'; break;
	}

	init_nno_saw(nno, 0);

	for (nni = nno->list, len = 0; nni->name; nni++) {
		if (nni->num == CSUM_gone)
			continue;
		if (nni->main_nni) {
			if (!dup_markup || nni->main_nni->num == CSUM_gone)
				continue;
			delim = dup_markup;
		}
		if (nni->num == 0 && !am_server && !dup_markup)
			continue;
		if (len)
			to_buf[len++]= ' ';
		if (delim) {
			to_buf[len++]= delim;
			delim = post_delim;
		}
		len += strlcpy(to_buf+len, nni->name, to_buf_len - len);
		if (len >= to_buf_len - 3)
			exit_cleanup(RERR_UNSUPPORTED); /* IMPOSSIBLE... */
		if (delim) {
			to_buf[len++]= delim;
			delim = '\0';
		}
		nno->saw[nni->num] = ++cnt;
	}

	return len;
}

static void send_negotiate_str(int f_out, struct name_num_obj *nno, int ntype)
{
	char tmpbuf[MAX_NSTR_STRLEN];
	const char *list_str = getenv_nstr(ntype);
	int len;

	if (list_str && *list_str) {
		init_nno_saw(nno, 0);
		len = parse_nni_str(nno, list_str, tmpbuf, MAX_NSTR_STRLEN);
		list_str = tmpbuf;
	} else
		list_str = NULL;

	if (!list_str || !*list_str)
		len = get_default_nno_list(nno, tmpbuf, MAX_NSTR_STRLEN, '\0');

	if (DEBUG_GTE(NSTR, am_server ? 3 : 2)) {
		if (am_server)
			rprintf(FINFO, "Server %s list (on server): %s\n", nno->type, tmpbuf);
		else
			rprintf(FINFO, "Client %s list (on client): %s\n", nno->type, tmpbuf);
	}

	/* Each side sends their list of valid names to the other side and then both sides
	 * pick the first name in the client's list that is also in the server's list. */
	if (do_negotiated_strings)
		write_vstring(f_out, tmpbuf, len);
}

static void negotiate_the_strings(int f_in, int f_out)
{
	/* We send all the negotiation strings before we start to read them to help avoid a slow startup. */

	init_checksum_choices();

	if (!checksum_choice)
		send_negotiate_str(f_out, &valid_checksums, NSTR_CHECKSUM);

	if (do_compression && !compress_choice)
		send_negotiate_str(f_out, &valid_compressions, NSTR_COMPRESS);

	if (valid_checksums.saw) {
		char tmpbuf[MAX_NSTR_STRLEN];
		int len;
		if (do_negotiated_strings)
			len = -1;
		else
			len = strlcpy(tmpbuf, protocol_version >= 30 ? "md5" : "md4", MAX_NSTR_STRLEN);
		recv_negotiate_str(f_in, &valid_checksums, tmpbuf, len);
	}

	if (valid_compressions.saw) {
		char tmpbuf[MAX_NSTR_STRLEN];
		int len;
		if (do_negotiated_strings)
			len = -1;
		else
			len = strlcpy(tmpbuf, "zlib", MAX_NSTR_STRLEN);
		recv_negotiate_str(f_in, &valid_compressions, tmpbuf, len);
	}

	/* If the other side is too old to negotiate, the above steps just made sure that
	 * the env didn't disallow the old algorithm. Mark things as non-negotiated. */
	if (!do_negotiated_strings)
		valid_checksums.negotiated_nni = valid_compressions.negotiated_nni = NULL;
}

void setup_protocol(int f_out,int f_in)
{
	assert(file_extra_cnt == 0);
	assert(EXTRA64_CNT == 2 || EXTRA64_CNT == 1);

	/* All int64 values must be set first so that they are guaranteed to be
	 * aligned for direct int64-pointer memory access. */
	if (preserve_atimes)
		atimes_ndx = (file_extra_cnt += EXTRA64_CNT);
	if (preserve_crtimes)
		crtimes_ndx = (file_extra_cnt += EXTRA64_CNT);
	if (am_sender) /* This is most likely in the file_extras64 union as well. */
		pathname_ndx = (file_extra_cnt += PTR_EXTRA_CNT);
	else
		depth_ndx = ++file_extra_cnt;
	if (preserve_uid)
		uid_ndx = ++file_extra_cnt;
	if (preserve_gid)
		gid_ndx = ++file_extra_cnt;
	if (preserve_acls && !am_sender)
		acls_ndx = ++file_extra_cnt;
	if (preserve_xattrs)
		xattrs_ndx = ++file_extra_cnt;

	if (am_server)
		set_allow_inc_recurse();

	if (remote_protocol == 0) {
		if (am_server && !local_server)
			check_sub_protocol();
		if (!read_batch)
			write_int(f_out, protocol_version);
		remote_protocol = read_int(f_in);
		if (protocol_version > remote_protocol)
			protocol_version = remote_protocol;
	}
	if (read_batch && remote_protocol > protocol_version) {
		rprintf(FERROR, "The protocol version in the batch file is too new (%d > %d).\n",
			remote_protocol, protocol_version);
		exit_cleanup(RERR_PROTOCOL);
	}

	if (DEBUG_GTE(PROTO, 1)) {
		rprintf(FINFO, "(%s) Protocol versions: remote=%d, negotiated=%d\n",
			am_server? "Server" : "Client", remote_protocol, protocol_version);
	}
	if (remote_protocol < MIN_PROTOCOL_VERSION
	 || remote_protocol > MAX_PROTOCOL_VERSION) {
		rprintf(FERROR,"protocol version mismatch -- is your shell clean?\n");
		rprintf(FERROR,"(see the rsync manpage for an explanation)\n");
		exit_cleanup(RERR_PROTOCOL);
	}
	if (remote_protocol < OLD_PROTOCOL_VERSION) {
		rprintf(FINFO,"%s is very old version of rsync, upgrade recommended.\n",
			am_server? "Client" : "Server");
	}
	if (protocol_version < MIN_PROTOCOL_VERSION) {
		rprintf(FERROR, "--protocol must be at least %d on the %s.\n",
			MIN_PROTOCOL_VERSION, am_server? "Server" : "Client");
		exit_cleanup(RERR_PROTOCOL);
	}
	if (protocol_version > PROTOCOL_VERSION) {
		rprintf(FERROR, "--protocol must be no more than %d on the %s.\n",
			PROTOCOL_VERSION, am_server? "Server" : "Client");
		exit_cleanup(RERR_PROTOCOL);
	}
	if (read_batch)
		check_batch_flags();

	if (!saw_stderr_opt && protocol_version <= 28 && am_server)
		msgs2stderr = 0; /* The client side may not have stderr setup for us. */

#ifndef SUPPORT_PREALLOCATION
	if (preallocate_files && !am_sender) {
		rprintf(FERROR, "preallocation is not supported on this %s\n",
			am_server ? "Server" : "Client");
		exit_cleanup(RERR_SYNTAX);
	}
#endif

	if (protocol_version < 30) {
		if (append_mode == 1)
			append_mode = 2;
		if (preserve_acls && !local_server) {
			rprintf(FERROR,
				"--acls requires protocol 30 or higher"
				" (negotiated %d).\n",
				protocol_version);
			exit_cleanup(RERR_PROTOCOL);
		}
		if (preserve_xattrs && !local_server) {
			rprintf(FERROR,
				"--xattrs requires protocol 30 or higher"
				" (negotiated %d).\n",
				protocol_version);
			exit_cleanup(RERR_PROTOCOL);
		}
	}

	if (delete_mode && !(delete_before+delete_during+delete_after)) {
		if (protocol_version < 30)
			delete_before = 1;
		else
			delete_during = 1;
	}

	if (protocol_version < 29) {
		if (fuzzy_basis) {
			rprintf(FERROR,
				"--fuzzy requires protocol 29 or higher"
				" (negotiated %d).\n",
				protocol_version);
			exit_cleanup(RERR_PROTOCOL);
		}

		if (basis_dir_cnt && inplace) {
			rprintf(FERROR,
				"%s with --inplace requires protocol 29 or higher"
				" (negotiated %d).\n",
				alt_dest_opt(0), protocol_version);
			exit_cleanup(RERR_PROTOCOL);
		}

		if (basis_dir_cnt > 1) {
			rprintf(FERROR,
				"Using more than one %s option requires protocol"
				" 29 or higher (negotiated %d).\n",
				alt_dest_opt(0), protocol_version);
			exit_cleanup(RERR_PROTOCOL);
		}

		if (prune_empty_dirs) {
			rprintf(FERROR,
				"--prune-empty-dirs requires protocol 29 or higher"
				" (negotiated %d).\n",
				protocol_version);
			exit_cleanup(RERR_PROTOCOL);
		}
	} else if (protocol_version >= 30) {
		if (am_server) {
			compat_flags = allow_inc_recurse ? CF_INC_RECURSE : 0;
#ifdef CAN_SET_SYMLINK_TIMES
			compat_flags |= CF_SYMLINK_TIMES;
#endif
#ifdef ICONV_OPTION
			compat_flags |= CF_SYMLINK_ICONV;
#endif
			if (strchr(client_info, 'f') != NULL)
				compat_flags |= CF_SAFE_FLIST;
			if (strchr(client_info, 'x') != NULL)
				compat_flags |= CF_AVOID_XATTR_OPTIM;
			if (strchr(client_info, 'C') != NULL)
				compat_flags |= CF_CHKSUM_SEED_FIX;
			if (strchr(client_info, 'I') != NULL)
				compat_flags |= CF_INPLACE_PARTIAL_DIR;
			if (strchr(client_info, 'u') != NULL)
				compat_flags |= CF_ID0_NAMES;
			if (strchr(client_info, 'v') != NULL) {
				do_negotiated_strings = 1;
				compat_flags |= CF_VARINT_FLIST_FLAGS;
			}
			if (strchr(client_info, 'V') != NULL) { /* Support a pre-release 'V' that got superseded */
				if (!write_batch)
					compat_flags |= CF_VARINT_FLIST_FLAGS;
				write_byte(f_out, compat_flags);
			} else
				write_varint(f_out, compat_flags);
		} else { /* read_varint() is compatible with the older write_byte() when the 0x80 bit isn't on. */
			compat_flags = read_varint(f_in);
			if  (compat_flags & CF_VARINT_FLIST_FLAGS)
				do_negotiated_strings = 1;
		}
		/* The inc_recurse var MUST be set to 0 or 1. */
		inc_recurse = compat_flags & CF_INC_RECURSE ? 1 : 0;
		want_xattr_optim = protocol_version >= 31 && !(compat_flags & CF_AVOID_XATTR_OPTIM);
		proper_seed_order = compat_flags & CF_CHKSUM_SEED_FIX ? 1 : 0;
		xfer_flags_as_varint = compat_flags & CF_VARINT_FLIST_FLAGS ? 1 : 0;
		xmit_id0_names = compat_flags & CF_ID0_NAMES ? 1 : 0;
		if (!xfer_flags_as_varint && preserve_crtimes) {
			fprintf(stderr, "Both rsync versions must be at least 3.2.0 for --crtimes.\n");
			exit_cleanup(RERR_PROTOCOL);
		}
		if (am_sender) {
			receiver_symlink_times = am_server
			    ? strchr(client_info, 'L') != NULL
			    : !!(compat_flags & CF_SYMLINK_TIMES);
		}
#ifdef CAN_SET_SYMLINK_TIMES
		else
			receiver_symlink_times = 1;
#endif
#ifdef ICONV_OPTION
		sender_symlink_iconv = iconv_opt && (am_server
		    ? strchr(client_info, 's') != NULL
		    : !!(compat_flags & CF_SYMLINK_ICONV));
#endif
		if (inc_recurse && !allow_inc_recurse) {
			/* This should only be able to happen in a batch. */
			fprintf(stderr,
				"Incompatible options specified for inc-recursive %s.\n",
				read_batch ? "batch file" : "connection");
			exit_cleanup(RERR_SYNTAX);
		}
		use_safe_inc_flist = (compat_flags & CF_SAFE_FLIST) || protocol_version >= 31;
		need_messages_from_generator = 1;
		if (compat_flags & CF_INPLACE_PARTIAL_DIR)
			inplace_partial = 1;
#ifdef CAN_SET_SYMLINK_TIMES
	} else if (!am_sender) {
		receiver_symlink_times = 1;
#endif
	}

	if (read_batch)
		do_negotiated_strings = 0;

	if (need_unsorted_flist && (!am_sender || inc_recurse))
		unsort_ndx = ++file_extra_cnt;

	if (partial_dir && *partial_dir != '/' && (!am_server || local_server)) {
		int rflags = FILTRULE_NO_PREFIXES | FILTRULE_DIRECTORY;
		if (!am_sender || protocol_version >= 30)
			rflags |= FILTRULE_PERISHABLE;
		parse_filter_str(&filter_list, partial_dir, rule_template(rflags), 0);
	}


#ifdef ICONV_OPTION
	if (protect_args && files_from) {
		if (am_sender)
			filesfrom_convert = filesfrom_host && ic_send != (iconv_t)-1;
		else
			filesfrom_convert = !filesfrom_host && ic_recv != (iconv_t)-1;
	}
#endif

	negotiate_the_strings(f_in, f_out);

	if (am_server) {
		if (!checksum_seed)
			checksum_seed = time(NULL) ^ (getpid() << 6);
		write_int(f_out, checksum_seed);
	} else {
		checksum_seed = read_int(f_in);
	}

	parse_checksum_choice(1); /* Sets file_sum_nni & xfer_sum_nni */
	parse_compress_choice(1); /* Sets do_compression */

	/* TODO in the future allow this algorithm to be chosen somehow, but it can't get too
	 * long or the size starts to cause a problem in the xattr abbrev/non-abbrev code. */
	xattr_sum_nni = parse_csum_name(NULL, 0);
	xattr_sum_len = csum_len_for_type(xattr_sum_nni->num, 0);

	if (write_batch && !am_server)
		write_batch_shell_file();

	init_flist();
}

void output_daemon_greeting(int f_out, int am_client)
{
	char tmpbuf[MAX_NSTR_STRLEN];
	int our_sub = get_subprotocol_version();

	init_checksum_choices();

	get_default_nno_list(&valid_auth_checksums, tmpbuf, MAX_NSTR_STRLEN, '\0');

	io_printf(f_out, "@RSYNCD: %d.%d %s\n", protocol_version, our_sub, tmpbuf);

	if (am_client && DEBUG_GTE(NSTR, 2))
		rprintf(FINFO, "Client %s list (on client): %s\n", valid_auth_checksums.type, tmpbuf);
}

void negotiate_daemon_auth(int f_out, int am_client)
{
	char tmpbuf[MAX_NSTR_STRLEN];
	int save_am_server = am_server;
	int md4_is_old = 0;

	if (!am_client)
		am_server = 1;

	if (daemon_auth_choices)
		strlcpy(tmpbuf, daemon_auth_choices, MAX_NSTR_STRLEN);
	else {
		strlcpy(tmpbuf, protocol_version >= 30 ? "md5" : "md4", MAX_NSTR_STRLEN);
		md4_is_old = 1;
	}

	if (am_client) {
		recv_negotiate_str(-1, &valid_auth_checksums, tmpbuf, strlen(tmpbuf));
		if (DEBUG_GTE(NSTR, 1)) {
			rprintf(FINFO, "Client negotiated %s: %s\n", valid_auth_checksums.type,
				valid_auth_checksums.negotiated_nni->name);
		}
	} else {
		if (!parse_negotiate_str(&valid_auth_checksums, tmpbuf)) {
			get_default_nno_list(&valid_auth_checksums, tmpbuf, MAX_NSTR_STRLEN, '\0');
			io_printf(f_out, "@ERROR: your client does not support one of our daemon-auth checksums: %s\n",
				  tmpbuf);
			exit_cleanup(RERR_UNSUPPORTED);
		}
	}
	am_server = save_am_server;
	if (md4_is_old && valid_auth_checksums.negotiated_nni->num == CSUM_MD4) {
		valid_auth_checksums.negotiated_nni->num = CSUM_MD4_OLD;
		valid_auth_checksums.negotiated_nni->flags = 0;
	}
}

int get_subprotocol_version()
{
#if SUBPROTOCOL_VERSION != 0
	return protocol_version < PROTOCOL_VERSION ? 0 : SUBPROTOCOL_VERSION;
#else
	return 0;
#endif
}
