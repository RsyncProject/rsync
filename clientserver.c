/* 
   Copyright (C) Andrew Tridgell 1998
   
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.
   
   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
*/

/* the socket based protocol for setting up a connection wit rsyncd */

#include "rsync.h"

extern int module_id;
extern int read_only;
extern int verbose;
extern int rsync_port;

int start_socket_client(char *host, char *path, int argc, char *argv[])
{
	int fd, i;
	char *sargs[MAX_ARGS];
	int sargc=0;
	char line[1024];
	char *p;
	int version;

	fd = open_socket_out(host, rsync_port);
	if (fd == -1) {
		exit_cleanup(1);
	}
	
	server_options(sargs,&sargc);

	sargs[sargc++] = ".";

	if (path && *path) 
		sargs[sargc++] = path;

	sargs[sargc] = NULL;

	if (!read_line(fd, line, sizeof(line)-1)) {
		return -1;
	}

	if (sscanf(line,"@RSYNCD: %d", &version) != 1) {
		return -1;
	}

	io_printf(fd,"@RSYNCD: %d\n", PROTOCOL_VERSION);

	p = strchr(path,'/');
	if (p) *p = 0;
	io_printf(fd,"%s\n",path);
	if (p) *p = '/';

	while (1) {
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}
		if (strcmp(line,"@RSYNCD: OK") == 0) break;
		rprintf(FINFO,"%s\n", line);
	}

	for (i=0;i<sargc;i++) {
		io_printf(fd,"%s\n", sargs[i]);
	}
	io_printf(fd,"\n");

#if 0
	while (1) {
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}
		rprintf(FINFO,"%s\n", line);
	}
#endif

	return client_run(fd, fd, -1, argc, argv);
}



static int rsync_module(int fd, int i)
{
	int argc=0;
	char *argv[MAX_ARGS];
	char **argp;
	char line[1024];
	uid_t uid;
	gid_t gid;
	char *p;
	char *addr = client_addr(fd);
	char *host = client_name(fd);

	if (!allow_access(addr, host, lp_hosts_allow(i), lp_hosts_deny(i))) {
		rprintf(FERROR,"rsync denied on module %s from %s (%s)\n",
			lp_name(i), client_name(fd), client_addr(fd));
		return -1;
	}

	rprintf(FINFO,"rsync on module %s from %s (%s)\n",
		lp_name(i), host, addr);

	module_id = i;

	if (lp_read_only(i))
		read_only = 1;

	p = lp_uid(i);
	if (!name_to_uid(p, &uid)) {
		if (!isdigit(*p)) {
			rprintf(FERROR,"Invalid uid %s\n", p);
			return -1;
		} 
		uid = atoi(p);
	}

	p = lp_gid(i);
	if (!name_to_gid(p, &gid)) {
		if (!isdigit(*p)) {
			rprintf(FERROR,"Invalid gid %s\n", p);
			return -1;
		} 
		gid = atoi(p);
	}

	if (chroot(lp_path(i))) {
		io_printf(fd,"@ERROR: chroot failed\n");
		return -1;
	}

	if (chdir("/")) {
		io_printf(fd,"@ERROR: chdir failed\n");
		return -1;
	}

	if (setgid(gid)) {
		io_printf(fd,"@ERROR: setgid failed\n");
		return -1;
	}

	if (setuid(uid)) {
		io_printf(fd,"@ERROR: setuid failed\n");
		return -1;
	}

	io_printf(fd,"@RSYNCD: OK\n");

	argv[argc++] = "rsyncd";

	while (1) {
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}

		if (!*line) break;

		argv[argc] = strdup(line);
		if (!argv[argc]) {
			return -1;
		}

		argc++;
		if (argc == MAX_ARGS) {
			return -1;
		}
	}

	parse_arguments(argc, argv);

	/* don't allow the logs to be flooded too fast */
	if (verbose > 1) verbose = 1;

	argc -= optind;
	argp = argv + optind;
	optind = 0;

	start_server(fd, fd, argc, argp);

	return 0;
}

/* send a list of available modules to the client. Don't list those
   with "list = False". */
static void send_listing(int fd)
{
	int n = lp_numservices();
	int i;
	
	for (i=0;i<n;i++)
		if (lp_list(i))
		    io_printf(fd, "%-15s\t%s\n", lp_name(i), lp_comment(i));
}

/* this is called when a socket connection is established to a client
   and we want to start talking. The setup of the system is done from
   here */
static int start_daemon(int fd)
{
	char line[200];
	char *motd;
	int version;
	int i = -1;
	extern char *config_file;

	if (!lp_load(config_file)) {
		exit_cleanup(1);
	}

	set_socket_options(fd,"SO_KEEPALIVE");

	io_printf(fd,"@RSYNCD: %d\n", PROTOCOL_VERSION);

	if (!read_line(fd, line, sizeof(line)-1)) {
		return -1;
	}

	if (sscanf(line,"@RSYNCD: %d", &version) != 1) {
		return -1;
	}	

	motd = lp_motd_file();
	if (*motd) {
		FILE *f = fopen(motd,"r");
		while (f && !feof(f)) {
			int len = fread(line, 1, sizeof(line)-1, f);
			if (len > 0) {
				line[len] = 0;
				io_printf(fd,"%s", line);
			}
		}
		if (f) fclose(f);
		io_printf(fd,"\n");
	}

	while (i == -1) {

		line[0] = 0;
		if (!read_line(fd, line, sizeof(line)-1)) {
			return -1;
		}

		if (!*line || strcmp(line,"#list")==0) {
			send_listing(fd);
			return -1;
		} 

		if (*line == '#') {
			/* it's some sort of command that I don't understand */
			io_printf(fd,"ERROR: Unknown command '%s'\n", line);
			return -1;
		}

		i = lp_number(line);
		if (i == -1) {
			io_printf(fd,"ERROR: Unknown module '%s'\n", line);
			return -1;
		}
	}

	return rsync_module(fd, i);
}


int daemon_main(void)
{
	if (is_a_socket(STDIN_FILENO)) {
		/* we are running via inetd */
		return start_daemon(STDIN_FILENO);
	}

	become_daemon();

	start_accept_loop(rsync_port, start_daemon);
	return -1;
}

