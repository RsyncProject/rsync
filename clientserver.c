/* -*- c-file-style: "linux"; -*-
 * 
 * Copyright (C) 1998-2001 by Andrew Tridgell <tridge@samba.org>
 * Copyright (C) 2001-2002 by Martin Pool <mbp@samba.org>
 * 
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 * 
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 * 
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

/**
 * @file
 *
 * The socket based protocol for setting up a connection with
 * rsyncd.
 **/

#include "rsync.h"

extern int module_id;
extern int read_only;
extern int verbose;
extern int rsync_port;
char *auth_user;
extern int sanitize_paths;

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
int start_socket_client(char *host, char *path, int argc, char *argv[])
{
	int fd, ret;
	char *p, *user=NULL;
	extern char *bind_address;
	extern int default_af_hint;
       
	/* this is redundant with code in start_inband_exchange(), but
	   this short-circuits a problem before we open a socket, and 
	   the extra check won't hurt */
	if (*path == '/') {
		rprintf(FERROR,"ERROR: The remote path must start with a module name not a /\n");
		return -1;
	}

	p = strchr(host, '@');
	if (p) {
		user = host;
		host = p+1;
		*p = 0;
	}

	if (verbose >= 2) {
		/* FIXME: If we're going to use a socket program for
		 * testing, then this message is wrong.  We need to
		 * say something like "(except really using %s)" */
		rprintf(FINFO, "opening tcp connection to %s port %d\n",
			host, rsync_port);
	}
	fd = open_socket_out_wrapped (host, rsync_port, bind_address,
				      default_af_hint);
	if (fd == -1) {
		exit_cleanup(RERR_SOCKETIO);
	}

	ret = start_inband_exchange(user, path, fd, fd, argc);

	return ret < 0? ret : client_run(fd, fd, -1, argc, argv);
}

int start_inband_exchange(char *user, char *path, int f_in, int f_out, int argc)
{
	int i;
	char *sargs[MAX_ARGS];
	int sargc = 0;
	char line[MAXPATHLEN];
	char *p;
	extern int remote_version;
	extern int kludge_around_eof;
	extern int am_sender;
	extern int daemon_over_rsh;
	extern int list_only;

	if (argc == 0 && !am_sender)
		list_only = 1;

	if (*path == '/') {
		rprintf(FERROR, "ERROR: The remote path must start with a module name\n");
		return -1;
	}

	if (!user) user = getenv("USER");
	if (!user) user = getenv("LOGNAME");

	/* set daemon_over_rsh to false since we need to build the 
	   true set of args passed through the rsh/ssh connection; 
	   this is a no-op for direct-socket-connection mode */
	daemon_over_rsh = 0;
	server_options(sargs, &sargc);

	sargs[sargc++] = ".";

	if (path && *path) 
		sargs[sargc++] = path;

	sargs[sargc] = NULL;

	io_printf(f_out, "@RSYNCD: %d\n", PROTOCOL_VERSION);

	if (!read_line(f_in, line, sizeof(line)-1)) {
		rprintf(FERROR, "rsync: did not see server greeting\n");
		return -1;
	}

	if (sscanf(line,"@RSYNCD: %d", &remote_version) != 1) {
		/* note that read_line strips of \n or \r */
		rprintf(FERROR, "rsync: server sent \"%s\" rather than greeting\n",
			line);
		return -1;
	}

	p = strchr(path,'/');
	if (p) *p = 0;
	io_printf(f_out, "%s\n", path);
	if (p) *p = '/';

	/* Old servers may just drop the connection here,
	 rather than sending a proper EXIT command.  Yuck. */
	kludge_around_eof = list_only && (remote_version < 25);

	while (1) {
		if (!read_line(f_in, line, sizeof(line)-1)) {
			rprintf(FERROR, "rsync: didn't get server startup line\n");
			return -1;
		}

		if (strncmp(line,"@RSYNCD: AUTHREQD ",18) == 0) {
			auth_client(f_out, user, line+18);
			continue;
		}

		if (strcmp(line,"@RSYNCD: OK") == 0) break;

		if (strcmp(line,"@RSYNCD: EXIT") == 0) {
			/* This is sent by recent versions of the
			 * server to terminate the listing of modules.
			 * We don't want to go on and transfer
			 * anything; just exit. */
			exit(0);
		}

		if (strncmp(line, "@ERROR", 6) == 0) {
			rprintf(FERROR,"%s\n", line);
			/* This is always fatal; the server will now
			 * close the socket. */
			return RERR_STARTCLIENT;
		} else {
			rprintf(FINFO,"%s\n", line);
		}
	}
	kludge_around_eof = False;

	for (i = 0; i < sargc; i++) {
		io_printf(f_out, "%s\n", sargs[i]);
	}
	io_printf(f_out, "\n");

	if (remote_version < 23) {
		if (remote_version == 22 || (remote_version > 17 && !am_sender))
			io_start_multiplex_in(f_in);
	}

	return 0;
}



static int rsync_module(int f_in, int f_out, int i)
{
	int argc=0;
	char *argv[MAX_ARGS];
	char **argp;
	char line[MAXPATHLEN];
	uid_t uid = (uid_t)-2;  /* canonically "nobody" */
	gid_t gid = (gid_t)-2;
	char *p;
	char *addr = client_addr(f_in);
	char *host = client_name(f_in);
	char *name = lp_name(i);
	int use_chroot = lp_use_chroot(i);
	int start_glob=0;
	int ret;
	char *request=NULL;
	extern int am_sender;
	extern int am_server;
	extern int am_daemon;
	extern int remote_version;
	extern int am_root;

	if (!allow_access(addr, host, lp_hosts_allow(i), lp_hosts_deny(i))) {
		rprintf(FERROR,"rsync denied on module %s from %s (%s)\n",
			name, host, addr);
		io_printf(f_out, "@ERROR: access denied to %s from %s (%s)\n",
			  name, host, addr);
		return -1;
	}

	if (am_daemon && am_server) {
		rprintf(FINFO, "rsync allowed access on module %s from %s (%s)\n",
			name, host, addr);
	}

	if (!claim_connection(lp_lock_file(i), lp_max_connections(i))) {
		if (errno) {
			rprintf(FERROR,"failed to open lock file %s : %s\n",
				lp_lock_file(i), strerror(errno));
			io_printf(f_out, "@ERROR: failed to open lock file %s : %s\n",
				  lp_lock_file(i), strerror(errno));
		} else {
			rprintf(FERROR,"max connections (%d) reached\n",
				lp_max_connections(i));
			io_printf(f_out, "@ERROR: max connections (%d) reached - try again later\n", lp_max_connections(i));
		}
		return -1;
	}

	
	auth_user = auth_server(f_in, f_out, i, addr, "@RSYNCD: AUTHREQD ");

	if (!auth_user) {
		rprintf(FERROR,"auth failed on module %s from %s (%s)\n",
			name, host, addr);
		io_printf(f_out, "@ERROR: auth failed on module %s\n", name);
		return -1;		
	}

	module_id = i;

	am_root = (getuid() == 0);

	if (am_root) {
		p = lp_uid(i);
		if (!name_to_uid(p, &uid)) {
			if (!isdigit(* (unsigned char *) p)) {
				rprintf(FERROR,"Invalid uid %s\n", p);
				io_printf(f_out, "@ERROR: invalid uid %s\n", p);
				return -1;
			} 
			uid = atoi(p);
		}

		p = lp_gid(i);
		if (!name_to_gid(p, &gid)) {
			if (!isdigit(* (unsigned char *) p)) {
				rprintf(FERROR,"Invalid gid %s\n", p);
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

	p = lp_include_from(i);
	add_exclude_file(p, 1, 1);

	p = lp_include(i);
	add_include_line(p);

	p = lp_exclude_from(i);
	add_exclude_file(p, 1, 0);

	p = lp_exclude(i);
	add_exclude_line(p);

	log_init();

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
		if (chroot(lp_path(i))) {
			rsyserr(FERROR, errno, "chroot %s failed", lp_path(i));
			io_printf(f_out, "@ERROR: chroot failed\n");
			return -1;
		}

		if (!push_dir("/", 0)) {
                        rsyserr(FERROR, errno, "chdir %s failed\n", lp_path(i));
			io_printf(f_out, "@ERROR: chdir failed\n");
			return -1;
		}

	} else {
		if (!push_dir(lp_path(i), 0)) {
			rsyserr(FERROR, errno, "chdir %s failed\n", lp_path(i));
			io_printf(f_out, "@ERROR: chdir failed\n");
			return -1;
		}
		sanitize_paths = 1;
	}

	if (am_root) {
#ifdef HAVE_SETGROUPS
		/* Get rid of any supplementary groups this process
		 * might have inheristed. */
		if (setgroups(0, NULL)) {
			rsyserr(FERROR, errno, "setgroups failed");
			io_printf(f_out, "@ERROR: setgroups failed\n");
			return -1;
		}
#endif

		/* XXXX: You could argue that if the daemon is started
		 * by a non-root user and they explicitly specify a
		 * gid, then we should try to change to that gid --
		 * this could be possible if it's already in their
		 * supplementary groups. */

		/* TODO: Perhaps we need to document that if rsyncd is
		 * started by somebody other than root it will inherit
		 * all their supplementary groups. */

		if (setgid(gid)) {
			rsyserr(FERROR, errno, "setgid %d failed", (int) gid);
			io_printf(f_out, "@ERROR: setgid failed\n");
			return -1;
		}

		if (setuid(uid)) {
			rsyserr(FERROR, errno, "setuid %d failed", (int) uid);
			io_printf(f_out, "@ERROR: setuid failed\n");
			return -1;
		}

		am_root = (getuid() == 0);
	}

	io_printf(f_out, "@RSYNCD: OK\n");

	argv[argc++] = "rsyncd";

	while (1) {
		if (!read_line(f_in, line, sizeof(line)-1)) {
			return -1;
		}

		if (!*line) break;

		p = line;

		argv[argc] = strdup(p);
		if (!argv[argc]) {
			return -1;
		}

		if (start_glob) {
			if (start_glob == 1) {
				request = strdup(p);
				start_glob++;
			}
			glob_expand(name, argv, &argc, MAX_ARGS);
		} else {
			argc++;
		}

		if (strcmp(line,".") == 0) {
			start_glob = 1;
		}

		if (argc == MAX_ARGS) {
			return -1;
		}
	}

	if (sanitize_paths) {
		/*
		 * Note that this is applied to all parameters, whether or not
		 *    they are filenames, but no other legal parameters contain
		 *    the forms that need to be sanitized so it doesn't hurt;
		 *    it is not known at this point which parameters are files
		 *    and which aren't.
		 */
		for (i = 1; i < argc; i++) {
			sanitize_path(argv[i], NULL);
		}
	}

        argp = argv;
	ret = parse_arguments(&argc, (const char ***) &argp, 0);

	if (request) {
		if (*auth_user) {
			rprintf(FINFO,"rsync %s %s from %s@%s (%s)\n",
				am_sender?"on":"to",
				request, auth_user, host, addr);
		} else {
			rprintf(FINFO,"rsync %s %s from %s (%s)\n",
				am_sender?"on":"to",
				request, host, addr);
		}
		free(request);
	}

#ifndef DEBUG
	/* don't allow the logs to be flooded too fast */
	if (verbose > 1) verbose = 1;
#endif

	if (remote_version < 23) {
		if (remote_version == 22 || (remote_version > 17 && am_sender))
			io_start_multiplex_out(f_out);
	}
        
        /* For later protocol versions, we don't start multiplexing
         * until we've configured nonblocking in start_server.  That
         * means we're in a sticky situation now: there's no way to
         * convey errors to the client. */

        /* FIXME: Hold off on reporting option processing errors until
         * we've set up nonblocking and multiplexed IO and can get the
         * message back to them. */
	if (!ret) {
                option_error();
                exit_cleanup(RERR_UNSUPPORTED);
	}

	if (lp_timeout(i)) {
		extern int io_timeout;
		io_timeout = lp_timeout(i);
	}

	start_server(f_in, f_out, argc, argp);

	return 0;
}

/* send a list of available modules to the client. Don't list those
   with "list = False". */
static void send_listing(int fd)
{
	int n = lp_numservices();
	int i;
	extern int remote_version;

	for (i=0;i<n;i++)
		if (lp_list(i))
		    io_printf(fd, "%-15s\t%s\n", lp_name(i), lp_comment(i));

	if (remote_version >= 25)
		io_printf(fd,"@RSYNCD: EXIT\n");
}

/* this is called when a connection is established to a client
   and we want to start talking. The setup of the system is done from
   here */
int start_daemon(int f_in, int f_out)
{
	char line[200];
	char *motd;
	int i = -1;
	extern char *config_file;
	extern int remote_version;
	extern int am_server;

	if (!lp_load(config_file, 0)) {
		exit_cleanup(RERR_SYNTAX);
	}

	log_init();

	if (!am_server) {
		set_socket_options(f_in, "SO_KEEPALIVE");
		set_socket_options(f_in, lp_socket_options());
		set_nonblocking(f_in);
	}

	io_printf(f_out, "@RSYNCD: %d\n", PROTOCOL_VERSION);

	motd = lp_motd_file();
	if (motd && *motd) {
		FILE *f = fopen(motd,"r");
		while (f && !feof(f)) {
			int len = fread(line, 1, sizeof(line)-1, f);
			if (len > 0) {
				line[len] = 0;
				io_printf(f_out, "%s", line);
			}
		}
		if (f) fclose(f);
		io_printf(f_out, "\n");
	}

	if (!read_line(f_in, line, sizeof(line)-1)) {
		return -1;
	}

	if (sscanf(line,"@RSYNCD: %d", &remote_version) != 1) {
		io_printf(f_out, "@ERROR: protocol startup error\n");
		return -1;
	}	

	while (i == -1) {
		line[0] = 0;
		if (!read_line(f_in, line, sizeof(line)-1)) {
			return -1;
		}

		if (!*line || strcmp(line,"#list")==0) {
			send_listing(f_out);
			return -1;
		} 

		if (*line == '#') {
			/* it's some sort of command that I don't understand */
			io_printf(f_out, "@ERROR: Unknown command '%s'\n", line);
			return -1;
		}

		i = lp_number(line);
		if (i == -1) {
			io_printf(f_out, "@ERROR: Unknown module '%s'\n", line);
			return -1;
		}
	}

	return rsync_module(f_in, f_out, i);
}


int daemon_main(void)
{
	extern char *config_file;
	extern int orig_umask;
	char *pid_file;
	extern int no_detach;

	if (is_a_socket(STDIN_FILENO)) {
		int i;

		/* we are running via inetd - close off stdout and
		   stderr so that library functions (and getopt) don't
		   try to use them. Redirect them to /dev/null */
		for (i=1;i<3;i++) {
			close(i); 
			open("/dev/null", O_RDWR);
		}

		return start_daemon(STDIN_FILENO, STDIN_FILENO);
	}

	if (!no_detach)
	    become_daemon();

	if (!lp_load(config_file, 1)) {
		exit_cleanup(RERR_SYNTAX);
	}

	log_init();

	rprintf(FINFO, "rsyncd version %s starting, listening on port %d\n",
		RSYNC_VERSION,
                rsync_port);
        /* TODO: If listening on a particular address, then show that
         * address too.  In fact, why not just do inet_ntop on the
         * local address??? */

	if (((pid_file = lp_pid_file()) != NULL) && (*pid_file != '\0')) {
		char pidbuf[16];
		int fd;
		int pid = (int) getpid();
		cleanup_set_pid(pid);
		if ((fd = do_open(lp_pid_file(), O_WRONLY|O_CREAT|O_TRUNC,
					0666 & ~orig_umask)) == -1) {
		    cleanup_set_pid(0);
		    rsyserr(FLOG, errno, "failed to create pid file %s", pid_file);
		    exit_cleanup(RERR_FILEIO);
		}
		snprintf(pidbuf, sizeof(pidbuf), "%d\n", pid);
		write(fd, pidbuf, strlen(pidbuf));
		close(fd);
	}

	start_accept_loop(rsync_port, start_daemon);
	return -1;
}

