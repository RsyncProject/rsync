/*
 * The socket based protocol for setting up a connection with rsyncd.
 *
 * Copyright (C) 1998-2001 Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2001-2002 Martin Pool <mbp@samba.org>
 * Copyright (C) 2002-2009 Wayne Davison
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
#include "ifuncs.h"

extern int quiet;
extern int verbose;
extern int dry_run;
extern int output_motd;
extern int list_only;
extern int am_sender;
extern int am_server;
extern int am_daemon;
extern int am_root;
extern int rsync_port;
extern int protect_args;
extern int ignore_errors;
extern int preserve_xattrs;
extern int kluge_around_eof;
extern int daemon_over_rsh;
extern int sanitize_paths;
extern int numeric_ids;
extern int filesfrom_fd;
extern int remote_protocol;
extern int protocol_version;
extern int io_timeout;
extern int no_detach;
extern int write_batch;
extern int default_af_hint;
extern int logfile_format_has_i;
extern int logfile_format_has_o_or_i;
extern mode_t orig_umask;
extern char *bind_address;
extern char *config_file;
extern char *logfile_format;
extern char *files_from;
extern char *tmpdir;
extern struct chmod_mode_struct *chmod_modes;
extern struct filter_list_struct daemon_filter_list;
#ifdef ICONV_OPTION
extern char *iconv_opt;
extern iconv_t ic_send, ic_recv;
#endif

char *auth_user;
int read_only = 0;
int module_id = -1;
int munge_symlinks = 0;
struct chmod_mode_struct *daemon_chmod_modes;

/* module_dirlen is the length of the module_dir string when in daemon
 * mode and module_dir is not "/"; otherwise 0.  (Note that a chroot-
 * enabled module can have a non-"/" module_dir these days.) */
char *module_dir = NULL;
unsigned int module_dirlen = 0;

char *full_module_path;

static int rl_nulls = 0;

#ifdef HAVE_SIGACTION
static struct sigaction sigact;
#endif

/**
 * Run a client connected to an rsyncd.  The alternative to this
 * function for remote-shell connections is do_cmd().
 *
 * After negotiating which module to use and reading the server's
 * motd, this hands over to client_run().  Telling the server the
 * module will cause it to chroot/setuid/etc.
 *
 * Instead of doing a transfer, the client may at this stage instead
 * get a listing of remote modules and exit.
 *
 * @return -1 for error in startup, or the result of client_run().
 * Either way, it eventually gets passed to exit_cleanup().
 **/
int start_socket_client(char *host, int remote_argc, char *remote_argv[],
			int argc, char *argv[])
{
	int fd, ret;
	char *p, *user = NULL;

	/* This is redundant with code in start_inband_exchange(), but this
	 * short-circuits a problem in the client before we open a socket,
	 * and the extra check won't hurt. */
	if (**remote_argv == '/') {
		rprintf(FERROR,
			"ERROR: The remote path must start with a module name not a /\n");
		return -1;
	}

	if ((p = strrchr(host, '@')) != NULL) {
		user = host;
		host = p+1;
		*p = '\0';
	}

	fd = open_socket_out_wrapped(host, rsync_port, bind_address,
				     default_af_hint);
	if (fd == -1)
		exit_cleanup(RERR_SOCKETIO);

#ifdef ICONV_CONST
	setup_iconv();
#endif

	ret = start_inband_exchange(fd, fd, user, remote_argc, remote_argv);

	return ret ? ret : client_run(fd, fd, -1, argc, argv);
}

static int exchange_protocols(int f_in, int f_out, char *buf, size_t bufsiz, int am_client)
{
	int remote_sub = -1;
#if SUBPROTOCOL_VERSION != 0
	int our_sub = protocol_version < PROTOCOL_VERSION ? 0 : SUBPROTOCOL_VERSION;
#else
	int our_sub = 0;
#endif
	char *motd;

	io_printf(f_out, "@RSYNCD: %d.%d\n", protocol_version, our_sub);

	if (!am_client) {
		motd = lp_motd_file();
		if (motd && *motd) {
			FILE *f = fopen(motd,"r");
			while (f && !feof(f)) {
				int len = fread(buf, 1, bufsiz - 1, f);
				if (len > 0)
					write_buf(f_out, buf, len);
			}
			if (f)
				fclose(f);
			write_sbuf(f_out, "\n");
		}
	}

	/* This strips the \n. */
	if (!read_line_old(f_in, buf, bufsiz)) {
		if (am_client)
			rprintf(FERROR, "rsync: did not see server greeting\n");
		return -1;
	}

	if (sscanf(buf, "@RSYNCD: %d.%d", &remote_protocol, &remote_sub) < 1) {
		if (am_client)
			rprintf(FERROR, "rsync: server sent \"%s\" rather than greeting\n", buf);
		else
			io_printf(f_out, "@ERROR: protocol startup error\n");
		return -1;
	}

	if (remote_sub < 0) {
		if (remote_protocol == 30) {
			if (am_client)
				rprintf(FERROR, "rsync: server is speaking an incompatible beta of protocol 30\n");
			else
				io_printf(f_out, "@ERROR: your client is speaking an incompatible beta of protocol 30\n");
			return -1;
		}
		remote_sub = 0;
	}

	if (protocol_version > remote_protocol) {
		protocol_version = remote_protocol;
		if (remote_sub)
			protocol_version--;
	} else if (protocol_version == remote_protocol) {
		if (remote_sub != our_sub)
			protocol_version--;
	}
#if SUBPROTOCOL_VERSION != 0
	else if (protocol_version < remote_protocol) {
		if (our_sub)
			protocol_version--;
	}
#endif

	if (protocol_version >= 30)
		rl_nulls = 1;

	return 0;
}

int start_inband_exchange(int f_in, int f_out, const char *user, int argc, char *argv[])
{
	int i, modlen;
	char line[BIGPATHBUFLEN];
	char *sargs[MAX_ARGS];
	int sargc = 0;
	char *p, *modname;

	assert(argc > 0 && *argv != NULL);

	if (**argv == '/') {
		rprintf(FERROR,
			"ERROR: The remote path must start with a module name\n");
		return -1;
	}

	if (!(p = strchr(*argv, '/')))
		modlen = strlen(*argv);
	else
		modlen = p - *argv;

	if (!(modname = new_array(char, modlen+1+1))) /* room for '/' & '\0' */
		out_of_memory("start_inband_exchange");
	strlcpy(modname, *argv, modlen + 1);
	modname[modlen] = '/';
	modname[modlen+1] = '\0';

	if (!user)
		user = getenv("USER");
	if (!user)
		user = getenv("LOGNAME");

	if (exchange_protocols(f_in, f_out, line, sizeof line, 1) < 0)
		return -1;

	/* set daemon_over_rsh to false since we need to build the
	 * true set of args passed through the rsh/ssh connection;
	 * this is a no-op for direct-socket-connection mode */
	daemon_over_rsh = 0;
	server_options(sargs, &sargc);

	if (sargc >= MAX_ARGS - 2)
		goto arg_overflow;

	sargs[sargc++] = ".";

	while (argc > 0) {
		if (sargc >= MAX_ARGS - 1) {
		  arg_overflow:
			rprintf(FERROR, "internal: args[] overflowed in do_cmd()\n");
			exit_cleanup(RERR_SYNTAX);
		}
		if (strncmp(*argv, modname, modlen) == 0
		 && argv[0][modlen] == '\0')
			sargs[sargc++] = modname; /* we send "modname/" */
		else
			sargs[sargc++] = *argv;
		argv++;
		argc--;
	}

	sargs[sargc] = NULL;

	if (verbose > 1)
		print_child_argv("sending daemon args:", sargs);

	io_printf(f_out, "%.*s\n", modlen, modname);

	/* Old servers may just drop the connection here,
	 rather than sending a proper EXIT command.  Yuck. */
	kluge_around_eof = list_only && protocol_version < 25 ? 1 : 0;

	while (1) {
		if (!read_line_old(f_in, line, sizeof line)) {
			rprintf(FERROR, "rsync: didn't get server startup line\n");
			return -1;
		}

		if (strncmp(line,"@RSYNCD: AUTHREQD ",18) == 0) {
			auth_client(f_out, user, line+18);
			continue;
		}

		if (strcmp(line,"@RSYNCD: OK") == 0)
			break;

		if (strcmp(line,"@RSYNCD: EXIT") == 0) {
			/* This is sent by recent versions of the
			 * server to terminate the listing of modules.
			 * We don't want to go on and transfer
			 * anything; just exit. */
			exit(0);
		}

		if (strncmp(line, "@ERROR", 6) == 0) {
			rprintf(FERROR, "%s\n", line);
			/* This is always fatal; the server will now
			 * close the socket. */
			return -1;
		}

		/* This might be a MOTD line or a module listing, but there is
		 * no way to differentiate it.  The manpage mentions this. */
		if (output_motd)
			rprintf(FINFO, "%s\n", line);
	}
	kluge_around_eof = 0;

	if (rl_nulls) {
		for (i = 0; i < sargc; i++) {
			if (!sargs[i]) /* stop at --protect-args NULL */
				break;
			write_sbuf(f_out, sargs[i]);
			write_byte(f_out, 0);
		}
		write_byte(f_out, 0);
	} else {
		for (i = 0; i < sargc; i++)
			io_printf(f_out, "%s\n", sargs[i]);
		write_sbuf(f_out, "\n");
	}

	if (protect_args)
		send_protected_args(f_out, sargs);

	if (protocol_version < 23) {
		if (protocol_version == 22 || !am_sender)
			io_start_multiplex_in();
	}

	free(modname);

	return 0;
}

static char *finish_pre_exec(pid_t pid, int fd, char *request,
			     char **early_argv, char **argv)
{
	int j = 0, status = -1;

	if (!request)
		request = "(NONE)";

	write_buf(fd, request, strlen(request)+1);
	if (early_argv) {
		for ( ; *early_argv; early_argv++)
			write_buf(fd, *early_argv, strlen(*early_argv)+1);
		j = 1; /* Skip arg0 name in argv. */
	}
	for ( ; argv[j]; j++) {
		write_buf(fd, argv[j], strlen(argv[j])+1);
		if (argv[j][0] == '.' && argv[j][1] == '\0')
			break;
	}
	write_byte(fd, 0);

	close(fd);

	if (wait_process(pid, &status, 0) < 0
	 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
		char *e;
		if (asprintf(&e, "pre-xfer exec returned failure (%d)%s%s\n",
			     status, status < 0 ? ": " : "",
			     status < 0 ? strerror(errno) : "") < 0)
			out_of_memory("finish_pre_exec");
		return e;
	}
	return NULL;
}

static int read_arg_from_pipe(int fd, char *buf, int limit)
{
	char *bp = buf, *eob = buf + limit - 1;

	while (1) {
	    int got = read(fd, bp, 1);
	    if (got != 1) {
		if (got < 0 && errno == EINTR)
			continue;
		return -1;
	    }
	    if (*bp == '\0')
		break;
	    if (bp < eob)
		bp++;
	}
	*bp = '\0';

	return bp - buf;
}

static int path_failure(int f_out, const char *dir, BOOL was_chdir)
{
	if (was_chdir)
		rsyserr(FLOG, errno, "chdir %s failed\n", dir);
	else
		rprintf(FLOG, "normalize_path(%s) failed\n", dir);
	io_printf(f_out, "@ERROR: chdir failed\n");
	return -1;
}

static int rsync_module(int f_in, int f_out, int i, char *addr, char *host)
{
	int argc;
	char **argv, **orig_argv, **orig_early_argv, *module_chdir;
	char line[BIGPATHBUFLEN];
	uid_t uid = (uid_t)-2;  /* canonically "nobody" */
	gid_t gid = (gid_t)-2;
	char *p, *err_msg = NULL;
	char *name = lp_name(i);
	int use_chroot = lp_use_chroot(i);
	int ret, pre_exec_fd = -1;
	pid_t pre_exec_pid = 0;
	char *request = NULL;

#ifdef ICONV_OPTION
	iconv_opt = lp_charset(i);
	if (*iconv_opt)
		setup_iconv();
	iconv_opt = NULL;
#endif

	if (!allow_access(addr, host, lp_hosts_allow(i), lp_hosts_deny(i))) {
		rprintf(FLOG, "rsync denied on module %s from %s (%s)\n",
			name, host, addr);
		if (!lp_list(i))
			io_printf(f_out, "@ERROR: Unknown module '%s'\n", name);
		else {
			io_printf(f_out,
				  "@ERROR: access denied to %s from %s (%s)\n",
				  name, host, addr);
		}
		return -1;
	}

	if (am_daemon && am_server) {
		rprintf(FLOG, "rsync allowed access on module %s from %s (%s)\n",
			name, host, addr);
	}

	if (!claim_connection(lp_lock_file(i), lp_max_connections(i))) {
		if (errno) {
			rsyserr(FLOG, errno, "failed to open lock file %s",
				lp_lock_file(i));
			io_printf(f_out, "@ERROR: failed to open lock file\n");
		} else {
			rprintf(FLOG, "max connections (%d) reached\n",
				lp_max_connections(i));
			io_printf(f_out, "@ERROR: max connections (%d) reached -- try again later\n",
				lp_max_connections(i));
		}
		return -1;
	}

	auth_user = auth_server(f_in, f_out, i, host, addr, "@RSYNCD: AUTHREQD ");

	if (!auth_user) {
		io_printf(f_out, "@ERROR: auth failed on module %s\n", name);
		return -1;
	}

	module_id = i;

	if (lp_read_only(i))
		read_only = 1;

	if (lp_transfer_logging(i) && !logfile_format)
		logfile_format = lp_log_format(i);
	if (log_format_has(logfile_format, 'i'))
		logfile_format_has_i = 1;
	if (logfile_format_has_i || log_format_has(logfile_format, 'o'))
		logfile_format_has_o_or_i = 1;

	am_root = (MY_UID() == 0);

	if (am_root) {
		p = lp_uid(i);
		if (!name_to_uid(p, &uid)) {
			if (!isDigit(p)) {
				rprintf(FLOG, "Invalid uid %s\n", p);
				io_printf(f_out, "@ERROR: invalid uid %s\n", p);
				return -1;
			}
			uid = atoi(p);
		}

		p = lp_gid(i);
		if (!name_to_gid(p, &gid)) {
			if (!isDigit(p)) {
				rprintf(FLOG, "Invalid gid %s\n", p);
				io_printf(f_out, "@ERROR: invalid gid %s\n", p);
				return -1;
			}
			gid = atoi(p);
		}
	}

	/* TODO: If we're not root, but the configuration requests
	 * that we change to some uid other than the current one, then
	 * log a warning. */

	/* TODO: Perhaps take a list of gids, and make them into the
	 * supplementary groups. */

	module_dir = lp_path(i);
	if (use_chroot) {
		if ((p = strstr(module_dir, "/./")) != NULL) {
			*p = '\0'; /* Temporary... */
			if (!(module_chdir = normalize_path(module_dir, True, NULL)))
				return path_failure(f_out, module_dir, False);
			*p = '/';
			if (!(p = normalize_path(p + 2, True, &module_dirlen)))
				return path_failure(f_out, strstr(module_dir, "/./"), False);
			if (!(full_module_path = normalize_path(module_dir, False, NULL)))
				full_module_path = module_dir;
			module_dir = p;
		} else {
			if (!(module_chdir = normalize_path(module_dir, False, NULL)))
				return path_failure(f_out, module_dir, False);
			full_module_path = module_chdir;
			module_dir = "/";
			module_dirlen = 1;
		}
	} else {
		if (!(module_chdir = normalize_path(module_dir, False, &module_dirlen)))
			return path_failure(f_out, module_dir, False);
		full_module_path = module_dir = module_chdir;
	}

	if (module_dirlen == 1) {
		module_dirlen = 0;
		set_filter_dir("/", 1);
	} else
		set_filter_dir(module_dir, module_dirlen);

	p = lp_filter(i);
	parse_rule(&daemon_filter_list, p, MATCHFLG_WORD_SPLIT,
		   XFLG_ABS_IF_SLASH | XFLG_DIR2WILD3);

	p = lp_include_from(i);
	parse_filter_file(&daemon_filter_list, p, MATCHFLG_INCLUDE,
	    XFLG_ABS_IF_SLASH | XFLG_DIR2WILD3 | XFLG_OLD_PREFIXES | XFLG_FATAL_ERRORS);

	p = lp_include(i);
	parse_rule(&daemon_filter_list, p,
		   MATCHFLG_INCLUDE | MATCHFLG_WORD_SPLIT,
		   XFLG_ABS_IF_SLASH | XFLG_DIR2WILD3 | XFLG_OLD_PREFIXES);

	p = lp_exclude_from(i);
	parse_filter_file(&daemon_filter_list, p, 0,
	    XFLG_ABS_IF_SLASH | XFLG_DIR2WILD3 | XFLG_OLD_PREFIXES | XFLG_FATAL_ERRORS);

	p = lp_exclude(i);
	parse_rule(&daemon_filter_list, p, MATCHFLG_WORD_SPLIT,
		   XFLG_ABS_IF_SLASH | XFLG_DIR2WILD3 | XFLG_OLD_PREFIXES);

	log_init(1);

#ifdef HAVE_PUTENV
	if (*lp_prexfer_exec(i) || *lp_postxfer_exec(i)) {
		char *modname, *modpath, *hostaddr, *hostname, *username;
		int status;

		if (asprintf(&modname, "RSYNC_MODULE_NAME=%s", name) < 0
		 || asprintf(&modpath, "RSYNC_MODULE_PATH=%s", full_module_path) < 0
		 || asprintf(&hostaddr, "RSYNC_HOST_ADDR=%s", addr) < 0
		 || asprintf(&hostname, "RSYNC_HOST_NAME=%s", host) < 0
		 || asprintf(&username, "RSYNC_USER_NAME=%s", auth_user) < 0)
			out_of_memory("rsync_module");
		putenv(modname);
		putenv(modpath);
		putenv(hostaddr);
		putenv(hostname);
		putenv(username);
		umask(orig_umask);
		/* For post-xfer exec, fork a new process to run the rsync
		 * daemon while this process waits for the exit status and
		 * runs the indicated command at that point. */
		if (*lp_postxfer_exec(i)) {
			pid_t pid = fork();
			if (pid < 0) {
				rsyserr(FLOG, errno, "fork failed");
				io_printf(f_out, "@ERROR: fork failed\n");
				return -1;
			}
			if (pid) {
				if (asprintf(&p, "RSYNC_PID=%ld", (long)pid) > 0)
					putenv(p);
				if (wait_process(pid, &status, 0) < 0)
					status = -1;
				if (asprintf(&p, "RSYNC_RAW_STATUS=%d", status) > 0)
					putenv(p);
				if (WIFEXITED(status))
					status = WEXITSTATUS(status);
				else
					status = -1;
				if (asprintf(&p, "RSYNC_EXIT_STATUS=%d", status) > 0)
					putenv(p);
				if (system(lp_postxfer_exec(i)) < 0)
					status = -1;
				_exit(status);
			}
		}
		/* For pre-xfer exec, fork a child process to run the indicated
		 * command, though it first waits for the parent process to
		 * send us the user's request via a pipe. */
		if (*lp_prexfer_exec(i)) {
			int fds[2];
			if (asprintf(&p, "RSYNC_PID=%ld", (long)getpid()) > 0)
				putenv(p);
			if (pipe(fds) < 0 || (pre_exec_pid = fork()) < 0) {
				rsyserr(FLOG, errno, "pre-xfer exec preparation failed");
				io_printf(f_out, "@ERROR: pre-xfer exec preparation failed\n");
				return -1;
			}
			if (pre_exec_pid == 0) {
				char buf[BIGPATHBUFLEN];
				int j, len;
				close(fds[1]);
				set_blocking(fds[0]);
				len = read_arg_from_pipe(fds[0], buf, BIGPATHBUFLEN);
				if (len <= 0)
					_exit(1);
				if (asprintf(&p, "RSYNC_REQUEST=%s", buf) > 0)
					putenv(p);
				for (j = 0; ; j++) {
					len = read_arg_from_pipe(fds[0], buf,
								 BIGPATHBUFLEN);
					if (len <= 0) {
						if (!len)
							break;
						_exit(1);
					}
					if (asprintf(&p, "RSYNC_ARG%d=%s", j, buf) > 0)
						putenv(p);
				}
				close(fds[0]);
				close(STDIN_FILENO);
				close(STDOUT_FILENO);
				status = system(lp_prexfer_exec(i));
				if (!WIFEXITED(status))
					_exit(1);
				_exit(WEXITSTATUS(status));
			}
			close(fds[0]);
			set_blocking(fds[1]);
			pre_exec_fd = fds[1];
		}
		umask(0);
	}
#endif

	if (use_chroot) {
		/*
		 * XXX: The 'use chroot' flag is a fairly reliable
		 * source of confusion, because it fails under two
		 * important circumstances: running as non-root,
		 * running on Win32 (or possibly others).  On the
		 * other hand, if you are running as root, then it
		 * might be better to always use chroot.
		 *
		 * So, perhaps if we can't chroot we should just issue
		 * a warning, unless a "require chroot" flag is set,
		 * in which case we fail.
		 */
		if (chroot(module_chdir)) {
			rsyserr(FLOG, errno, "chroot %s failed", module_chdir);
			io_printf(f_out, "@ERROR: chroot failed\n");
			return -1;
		}
		module_chdir = module_dir;
	}

	if (!change_dir(module_chdir, CD_NORMAL))
		return path_failure(f_out, module_chdir, True);
	if (module_dirlen || !use_chroot)
		sanitize_paths = 1;

	if ((munge_symlinks = lp_munge_symlinks(i)) < 0)
		munge_symlinks = !use_chroot || module_dirlen;
	if (munge_symlinks) {
		STRUCT_STAT st;
		if (do_stat(SYMLINK_PREFIX, &st) == 0 && S_ISDIR(st.st_mode)) {
			rprintf(FLOG, "Symlink munging is unsupported when a %s directory exists.\n",
				SYMLINK_PREFIX);
			io_printf(f_out, "@ERROR: daemon security issue -- contact admin\n", name);
			exit_cleanup(RERR_UNSUPPORTED);
		}
	}

	if (am_root) {
		/* XXXX: You could argue that if the daemon is started
		 * by a non-root user and they explicitly specify a
		 * gid, then we should try to change to that gid --
		 * this could be possible if it's already in their
		 * supplementary groups. */

		/* TODO: Perhaps we need to document that if rsyncd is
		 * started by somebody other than root it will inherit
		 * all their supplementary groups. */

		if (setgid(gid)) {
			rsyserr(FLOG, errno, "setgid %d failed", (int)gid);
			io_printf(f_out, "@ERROR: setgid failed\n");
			return -1;
		}
#ifdef HAVE_SETGROUPS
		/* Get rid of any supplementary groups this process
		 * might have inheristed. */
		if (setgroups(1, &gid)) {
			rsyserr(FLOG, errno, "setgroups failed");
			io_printf(f_out, "@ERROR: setgroups failed\n");
			return -1;
		}
#endif

		if (setuid(uid) < 0
#ifdef HAVE_SETEUID
		 || seteuid(uid) < 0
#endif
		) {
			rsyserr(FLOG, errno, "setuid %d failed", (int)uid);
			io_printf(f_out, "@ERROR: setuid failed\n");
			return -1;
		}

		am_root = (MY_UID() == 0);
	}

	if (lp_temp_dir(i) && *lp_temp_dir(i)) {
		tmpdir = lp_temp_dir(i);
		if (strlen(tmpdir) >= MAXPATHLEN - 10) {
			rprintf(FLOG,
				"the 'temp dir' value for %s is WAY too long -- ignoring.\n",
				name);
			tmpdir = NULL;
		}
	}

	io_printf(f_out, "@RSYNCD: OK\n");

	read_args(f_in, name, line, sizeof line, rl_nulls, &argv, &argc, &request);
	orig_argv = argv;

	verbose = 0; /* future verbosity is controlled by client options */
	ret = parse_arguments(&argc, (const char ***) &argv);
	if (protect_args && ret) {
		orig_early_argv = orig_argv;
		protect_args = 2;
		read_args(f_in, name, line, sizeof line, 1, &argv, &argc, &request);
		orig_argv = argv;
		ret = parse_arguments(&argc, (const char ***) &argv);
	} else
		orig_early_argv = NULL;

	if (pre_exec_pid) {
		err_msg = finish_pre_exec(pre_exec_pid, pre_exec_fd, request,
					  orig_early_argv, orig_argv);
	}

	if (orig_early_argv)
		free(orig_early_argv);

	am_server = 1; /* Don't let someone try to be tricky. */
	quiet = 0;
	if (lp_ignore_errors(module_id))
		ignore_errors = 1;
	if (write_batch < 0)
		dry_run = 1;

	if (lp_fake_super(i)) {
		if (preserve_xattrs > 1)
			preserve_xattrs = 1;
		am_root = -1;
	} else if (am_root < 0) /* Treat --fake-super from client as --super. */
		am_root = 2;

	if (filesfrom_fd == 0)
		filesfrom_fd = f_in;

	if (request) {
		if (*auth_user) {
			rprintf(FLOG, "rsync %s %s from %s@%s (%s)\n",
				am_sender ? "on" : "to",
				request, auth_user, host, addr);
		} else {
			rprintf(FLOG, "rsync %s %s from %s (%s)\n",
				am_sender ? "on" : "to",
				request, host, addr);
		}
		free(request);
	}

#ifndef DEBUG
	/* don't allow the logs to be flooded too fast */
	if (verbose > lp_max_verbosity(i))
		verbose = lp_max_verbosity(i);
#endif

	if (protocol_version < 23
	    && (protocol_version == 22 || am_sender))
		io_start_multiplex_out();
	else if (!ret || err_msg) {
		/* We have to get I/O multiplexing started so that we can
		 * get the error back to the client.  This means getting
		 * the protocol setup finished first in later versions. */
		setup_protocol(f_out, f_in);
		if (!am_sender) {
			/* Since we failed in our option parsing, we may not
			 * have finished parsing that the client sent us a
			 * --files-from option, so look for it manually.
			 * Without this, the socket would be in the wrong
			 * state for the upcoming error message. */
			if (!files_from) {
				int i;
				for (i = 0; i < argc; i++) {
					if (strncmp(argv[i], "--files-from", 12) == 0) {
						files_from = "";
						break;
					}
				}
			}
			if (files_from)
				write_byte(f_out, 0);
		}
		io_start_multiplex_out();
	}

	if (!ret || err_msg) {
		if (err_msg)
			rwrite(FERROR, err_msg, strlen(err_msg), 0);
		else
			option_error();
		msleep(400);
		exit_cleanup(RERR_UNSUPPORTED);
	}

#ifdef ICONV_OPTION
	if (!iconv_opt) {
		if (ic_send != (iconv_t)-1) {
			iconv_close(ic_send);
			ic_send = (iconv_t)-1;
		}
		if (ic_recv != (iconv_t)-1) {
			iconv_close(ic_recv);
			ic_recv = (iconv_t)-1;
		}
	}
#endif

	if (!numeric_ids
	 && (use_chroot ? lp_numeric_ids(i) != False : lp_numeric_ids(i) == True))
		numeric_ids = -1; /* Set --numeric-ids w/o breaking protocol. */

	if (lp_timeout(i) && (!io_timeout || lp_timeout(i) < io_timeout))
		set_io_timeout(lp_timeout(i));

	/* If we have some incoming/outgoing chmod changes, append them to
	 * any user-specified changes (making our changes have priority).
	 * We also get a pointer to just our changes so that a receiver
	 * process can use them separately if --perms wasn't specified. */
	if (am_sender)
		p = lp_outgoing_chmod(i);
	else
		p = lp_incoming_chmod(i);
	if (*p && !(daemon_chmod_modes = parse_chmod(p, &chmod_modes))) {
		rprintf(FLOG, "Invalid \"%sing chmod\" directive: %s\n",
			am_sender ? "outgo" : "incom", p);
	}

	start_server(f_in, f_out, argc, argv);

	return 0;
}

/* send a list of available modules to the client. Don't list those
   with "list = False". */
static void send_listing(int fd)
{
	int n = lp_numservices();
	int i;

	for (i = 0; i < n; i++) {
		if (lp_list(i))
			io_printf(fd, "%-15s\t%s\n", lp_name(i), lp_comment(i));
	}

	if (protocol_version >= 25)
		io_printf(fd,"@RSYNCD: EXIT\n");
}

static int load_config(int globals_only)
{
	if (!config_file) {
		if (am_server && am_root <= 0)
			config_file = RSYNCD_USERCONF;
		else
			config_file = RSYNCD_SYSCONF;
	}
	return lp_load(config_file, globals_only);
}

/* this is called when a connection is established to a client
   and we want to start talking. The setup of the system is done from
   here */
int start_daemon(int f_in, int f_out)
{
	char line[1024];
	char *addr, *host;
	int i;

	io_set_sock_fds(f_in, f_out);

	/* We must load the config file before calling any function that
	 * might cause log-file output to occur.  This ensures that the
	 * "log file" param gets honored for the 2 non-forked use-cases
	 * (when rsync is run by init and run by a remote shell). */
	if (!load_config(0))
		exit_cleanup(RERR_SYNTAX);

	addr = client_addr(f_in);
	host = client_name(f_in);
	rprintf(FLOG, "connect from %s (%s)\n", host, addr);

	if (!am_server) {
		set_socket_options(f_in, "SO_KEEPALIVE");
		set_nonblocking(f_in);
	}

	if (exchange_protocols(f_in, f_out, line, sizeof line, 0) < 0)
		return -1;

	line[0] = 0;
	if (!read_line_old(f_in, line, sizeof line))
		return -1;

	if (!*line || strcmp(line, "#list") == 0) {
		rprintf(FLOG, "module-list request from %s (%s)\n",
			host, addr);
		send_listing(f_out);
		return -1;
	}

	if (*line == '#') {
		/* it's some sort of command that I don't understand */
		io_printf(f_out, "@ERROR: Unknown command '%s'\n", line);
		return -1;
	}

	if ((i = lp_number(line)) < 0) {
		rprintf(FLOG, "unknown module '%s' tried from %s (%s)\n",
			line, host, addr);
		io_printf(f_out, "@ERROR: Unknown module '%s'\n", line);
		return -1;
	}

#ifdef HAVE_SIGACTION
	sigact.sa_flags = SA_NOCLDSTOP;
#endif
	SIGACTION(SIGCHLD, remember_children);

	return rsync_module(f_in, f_out, i, addr, host);
}

static void create_pid_file(void)
{
	char *pid_file = lp_pid_file();
	char pidbuf[16];
	pid_t pid = getpid();
	int fd, len;

	if (!pid_file || !*pid_file)
		return;

	cleanup_set_pid(pid);
	if ((fd = do_open(pid_file, O_WRONLY|O_CREAT|O_EXCL, 0666 & ~orig_umask)) == -1) {
	  failure:
		cleanup_set_pid(0);
		fprintf(stderr, "failed to create pid file %s: %s\n", pid_file, strerror(errno));
		rsyserr(FLOG, errno, "failed to create pid file %s", pid_file);
		exit_cleanup(RERR_FILEIO);
	}
	snprintf(pidbuf, sizeof pidbuf, "%ld\n", (long)pid);
	len = strlen(pidbuf);
	if (write(fd, pidbuf, len) != len)
		goto failure;
	close(fd);
}

/* Become a daemon, discarding the controlling terminal. */
static void become_daemon(void)
{
	int i;
	pid_t pid = fork();

	if (pid) {
		if (pid < 0) {
			fprintf(stderr, "failed to fork: %s\n", strerror(errno));
			exit_cleanup(RERR_FILEIO);
		}
		_exit(0);
	}

	create_pid_file();

	/* detach from the terminal */
#ifdef HAVE_SETSID
	setsid();
#elif defined TIOCNOTTY
	i = open("/dev/tty", O_RDWR);
	if (i >= 0) {
		ioctl(i, (int)TIOCNOTTY, (char *)0);
		close(i);
	}
#endif
	/* make sure that stdin, stdout an stderr don't stuff things
	 * up (library functions, for example) */
	for (i = 0; i < 3; i++) {
		close(i);
		open("/dev/null", O_RDWR);
	}
}

int daemon_main(void)
{
	if (is_a_socket(STDIN_FILENO)) {
		int i;

		/* we are running via inetd - close off stdout and
		 * stderr so that library functions (and getopt) don't
		 * try to use them. Redirect them to /dev/null */
		for (i = 1; i < 3; i++) {
			close(i);
			open("/dev/null", O_RDWR);
		}

		return start_daemon(STDIN_FILENO, STDIN_FILENO);
	}

	if (!load_config(1)) {
		fprintf(stderr, "Failed to parse config file: %s\n", config_file);
		exit_cleanup(RERR_SYNTAX);
	}

	if (no_detach)
		create_pid_file();
	else
		become_daemon();

	if (rsync_port == 0 && (rsync_port = lp_rsync_port()) == 0)
		rsync_port = RSYNC_PORT;
	if (bind_address == NULL && *lp_bind_address())
		bind_address = lp_bind_address();

	log_init(0);

	rprintf(FLOG, "rsyncd version %s starting, listening on port %d\n",
		RSYNC_VERSION, rsync_port);
	/* TODO: If listening on a particular address, then show that
	 * address too.  In fact, why not just do inet_ntop on the
	 * local address??? */

	start_accept_loop(rsync_port, start_daemon);
	return -1;
}
