/* -*- c-file-style: "linux"; -*-
   
   Copyright (C) 1998-2000 by Andrew Tridgell 
   
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

/* support rsync authentication */
#include "rsync.h"

/***************************************************************************
encode a buffer using base64 - simple and slow algorithm. null terminates
the result.
  ***************************************************************************/
static void base64_encode(char *buf, int len, char *out)
{
	char *b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	int bit_offset, byte_offset, idx, i;
	unsigned char *d = (unsigned char *)buf;
	int bytes = (len*8 + 5)/6;

	memset(out, 0, bytes+1);

	for (i=0;i<bytes;i++) {
		byte_offset = (i*6)/8;
		bit_offset = (i*6)%8;
		if (bit_offset < 3) {
			idx = (d[byte_offset] >> (2-bit_offset)) & 0x3F;
		} else {
			idx = (d[byte_offset] << (bit_offset-2)) & 0x3F;
			if (byte_offset+1 < len) {
				idx |= (d[byte_offset+1] >> (8-(bit_offset-2)));
			}
		}
		out[i] = b64[idx];
	}
}

/* create a 16 byte challenge buffer */
static void gen_challenge(char *addr, char *challenge)
{
	char input[32];
	struct timeval tv;

	memset(input, 0, sizeof(input));

	strlcpy((char *)input, addr, 17);
	sys_gettimeofday(&tv);
	SIVAL(input, 16, tv.tv_sec);
	SIVAL(input, 20, tv.tv_usec);
	SIVAL(input, 24, getpid());

	sum_init();
	sum_update(input, sizeof(input));
	sum_end(challenge);
}


/* return the secret for a user from the sercret file. maximum length
   is len. null terminate it */
static int get_secret(int module, char *user, char *secret, int len)
{
	char *fname = lp_secrets_file(module);
	int fd, found=0;
	char line[MAXPATHLEN];
	char *p, *pass=NULL;
	STRUCT_STAT st;
	int ok = 1;
	extern int am_root;

	if (!fname || !*fname) return 0;

	fd = open(fname,O_RDONLY);
	if (fd == -1) return 0;

	if (do_stat(fname, &st) == -1) {
		rsyserr(FERROR, errno, "stat(%s)", fname);
		ok = 0;
	} else if (lp_strict_modes(module)) {
		if ((st.st_mode & 06) != 0) {
			rprintf(FERROR,"secrets file must not be other-accessible (see strict modes option)\n");
			ok = 0;
		} else if (am_root && (st.st_uid != 0)) {
			rprintf(FERROR,"secrets file must be owned by root when running as root (see strict modes)\n");
			ok = 0;
		}
	}
	if (!ok) {
		rprintf(FERROR,"continuing without secrets file\n");
		close(fd);
		return 0;
	}

	while (!found) {
		int i = 0;
		memset(line, 0, sizeof line);
		while ((size_t) i < (sizeof(line)-1)) {
			if (read(fd, &line[i], 1) != 1) {
				memset(line, 0, sizeof(line));
				close(fd);
				return 0;
			}
			if (line[i] == '\r') continue;
			if (line[i] == '\n') break;
			i++;
		}
		line[i] = 0;
		if (line[0] == '#') continue;
		p = strchr(line,':');
		if (!p) continue;
		*p = 0;
		if (strcmp(user, line)) continue;
		pass = p+1;
		found = 1;
	}

	close(fd);
	if (!found) return 0;

	strlcpy(secret, pass, len);
	return 1;
}

static char *getpassf(char *filename)
{
	char buffer[100];
	int fd=0;
	STRUCT_STAT st;
	int ok = 1;
	extern int am_root;
	char *envpw=getenv("RSYNC_PASSWORD");

	if (!filename) return NULL;

	if ( (fd=open(filename,O_RDONLY)) == -1) {
		rsyserr(FERROR, errno, "could not open password file \"%s\"",filename);
		if (envpw) rprintf(FERROR,"falling back to RSYNC_PASSWORD environment variable.\n");	
		return NULL;
	}
	
	if (do_stat(filename, &st) == -1) {
		rsyserr(FERROR, errno, "stat(%s)", filename);
		ok = 0;
	} else if ((st.st_mode & 06) != 0) {
		rprintf(FERROR,"password file must not be other-accessible\n");
		ok = 0;
	} else if (am_root && (st.st_uid != 0)) {
		rprintf(FERROR,"password file must be owned by root when running as root\n");
		ok = 0;
	}
	if (!ok) {
		rprintf(FERROR,"continuing without password file\n");
		if (envpw) rprintf(FERROR,"using RSYNC_PASSWORD environment variable.\n");
		close(fd);
		return NULL;
	}

	if (envpw) rprintf(FERROR,"RSYNC_PASSWORD environment variable ignored\n");

	buffer[sizeof(buffer)-1]='\0';
	if (read(fd,buffer,sizeof(buffer)-1) > 0)
	{
		char *p = strtok(buffer,"\n\r");
		close(fd);
		if (p) p = strdup(p);
		return p;
	}	

	return NULL;
}

/* generate a 16 byte hash from a password and challenge */
static void generate_hash(char *in, char *challenge, char *out)
{
	char buf[16];

	sum_init();
	sum_update(in, strlen(in));
	sum_update(challenge, strlen(challenge));
	sum_end(buf);

	base64_encode(buf, 16, out);
}

/* possible negotiate authentication with the client. Use "leader" to
   start off the auth if necessary 

   return NULL if authentication failed

   return "" if anonymous access

   otherwise return username
*/
char *auth_server(int f_in, int f_out, int module, char *addr, char *leader)
{
	char *users = lp_auth_users(module);
	char challenge[16];
	char b64_challenge[30];
	char line[MAXPATHLEN];
	static char user[100];
	char secret[100];
	char pass[30];
	char pass2[30];
	char *tok;

	/* if no auth list then allow anyone in! */
	if (!users || !*users) return "";

	gen_challenge(addr, challenge);
	
	base64_encode(challenge, 16, b64_challenge);

	io_printf(f_out, "%s%s\n", leader, b64_challenge);

	if (!read_line(f_in, line, sizeof(line)-1)) {
		return NULL;
	}

	memset(user, 0, sizeof(user));
	memset(pass, 0, sizeof(pass));

	if (sscanf(line,"%99s %29s", user, pass) != 2) {
		return NULL;
	}
	
	users = strdup(users);
	if (!users) return NULL;

	for (tok=strtok(users," ,\t"); tok; tok = strtok(NULL," ,\t")) {
		if (fnmatch(tok, user, 0) == 0) break;
	}
	free(users);

	if (!tok) {
		return NULL;
	}
	
	memset(secret, 0, sizeof(secret));
	if (!get_secret(module, user, secret, sizeof(secret)-1)) {
		memset(secret, 0, sizeof(secret));
		return NULL;
	}

	generate_hash(secret, b64_challenge, pass2);
	memset(secret, 0, sizeof(secret));
	
	if (strcmp(pass, pass2) == 0)
		return user;

	return NULL;
}


void auth_client(int fd, char *user, char *challenge)
{
	char *pass;
	char pass2[30];
	extern char *password_file;

	if (!user || !*user) return;

	if (!(pass=getpassf(password_file)) && !(pass=getenv("RSYNC_PASSWORD"))) {
		/* XXX: cyeoh says that getpass is deprecated, because
		   it may return a truncated password on some systems,
		   and it is not in the LSB. */
		pass = getpass("Password: ");
	}

	if (!pass || !*pass) {
		pass = "";
	}

	generate_hash(pass, challenge, pass2);
	io_printf(fd, "%s %s\n", user, pass2);
}


