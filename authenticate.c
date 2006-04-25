/*
 * Support rsync daemon authentication.
 *
 * Copyright (C) 1998-2000 Andrew Tridgell
 * Copyright (C) 2002, 2004, 2005, 2006 Wayne Davison
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
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street - Fifth Floor, Boston, MA 02110-1301, USA.
 */

#include "rsync.h"

extern char *password_file;

/***************************************************************************
encode a buffer using base64 - simple and slow algorithm. null terminates
the result.
  ***************************************************************************/
void base64_encode(char *buf, int len, char *out, int pad)
{
	char *b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	int bit_offset, byte_offset, idx, i;
	unsigned char *d = (unsigned char *)buf;
	int bytes = (len*8 + 5)/6;

	for (i = 0; i < bytes; i++) {
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

	while (pad && (i % 4))
		out[i++] = '=';

	out[i] = '\0';
}

/* Generate a challenge buffer and return it base64-encoded. */
static void gen_challenge(char *addr, char *challenge)
{
	char input[32];
	char md4_out[MD4_SUM_LENGTH];
	struct timeval tv;

	memset(input, 0, sizeof input);

	strlcpy((char *)input, addr, 17);
	sys_gettimeofday(&tv);
	SIVAL(input, 16, tv.tv_sec);
	SIVAL(input, 20, tv.tv_usec);
	SIVAL(input, 24, getpid());

	sum_init(0);
	sum_update(input, sizeof input);
	sum_end(md4_out);

	base64_encode(md4_out, MD4_SUM_LENGTH, challenge, 0);
}


/* Return the secret for a user from the secret file, null terminated.
 * Maximum length is len (not counting the null). */
static int get_secret(int module, char *user, char *secret, int len)
{
	char *fname = lp_secrets_file(module);
	STRUCT_STAT st;
	int fd, ok = 1;
	char ch, *p;

	if (!fname || !*fname)
		return 0;

	if ((fd = open(fname, O_RDONLY)) < 0)
		return 0;

	if (do_stat(fname, &st) == -1) {
		rsyserr(FLOG, errno, "stat(%s)", fname);
		ok = 0;
	} else if (lp_strict_modes(module)) {
		if ((st.st_mode & 06) != 0) {
			rprintf(FLOG, "secrets file must not be other-accessible (see strict modes option)\n");
			ok = 0;
		} else if (MY_UID() == 0 && st.st_uid != 0) {
			rprintf(FLOG, "secrets file must be owned by root when running as root (see strict modes)\n");
			ok = 0;
		}
	}
	if (!ok) {
		rprintf(FLOG, "continuing without secrets file\n");
		close(fd);
		return 0;
	}

	if (*user == '#') {
		/* Reject attempt to match a comment. */
		close(fd);
		return 0;
	}

	/* Try to find a line that starts with the user name and a ':'. */
	p = user;
	while (1) {
		if (read(fd, &ch, 1) != 1) {
			close(fd);
			return 0;
		}
		if (ch == '\n')
			p = user;
		else if (p) {
			if (*p == ch)
				p++;
			else if (!*p && ch == ':')
				break;
			else
				p = NULL;
		}
	}

	/* Slurp the secret into the "secret" buffer. */
	p = secret;
	while (len > 0) {
		if (read(fd, p, 1) != 1 || *p == '\n')
			break;
		if (*p == '\r')
			continue;
		p++;
		len--;
	}
	*p = '\0';
	close(fd);

	return 1;
}

static char *getpassf(char *filename)
{
	STRUCT_STAT st;
	char buffer[512], *p;
	int fd, n, ok = 1;
	char *envpw = getenv("RSYNC_PASSWORD");

	if (!filename)
		return NULL;

	if ((fd = open(filename,O_RDONLY)) < 0) {
		rsyserr(FERROR, errno, "could not open password file \"%s\"",
			filename);
		if (envpw)
			rprintf(FERROR, "falling back to RSYNC_PASSWORD environment variable.\n");
		return NULL;
	}

	if (do_stat(filename, &st) == -1) {
		rsyserr(FERROR, errno, "stat(%s)", filename);
		ok = 0;
	} else if ((st.st_mode & 06) != 0) {
		rprintf(FERROR,"password file must not be other-accessible\n");
		ok = 0;
	} else if (MY_UID() == 0 && st.st_uid != 0) {
		rprintf(FERROR,"password file must be owned by root when running as root\n");
		ok = 0;
	}
	if (!ok) {
		rprintf(FERROR,"continuing without password file\n");
		if (envpw)
			rprintf(FERROR, "using RSYNC_PASSWORD environment variable.\n");
		close(fd);
		return NULL;
	}

	if (envpw)
		rprintf(FERROR, "RSYNC_PASSWORD environment variable ignored\n");

	n = read(fd, buffer, sizeof buffer - 1);
	close(fd);
	if (n > 0) {
		buffer[n] = '\0';
		if ((p = strtok(buffer, "\n\r")) != NULL)
			return strdup(p);
	}

	return NULL;
}

/* Generate an MD4 hash created from the combination of the password
 * and the challenge string and return it base64-encoded. */
static void generate_hash(char *in, char *challenge, char *out)
{
	char buf[MD4_SUM_LENGTH];

	sum_init(0);
	sum_update(in, strlen(in));
	sum_update(challenge, strlen(challenge));
	sum_end(buf);

	base64_encode(buf, MD4_SUM_LENGTH, out, 0);
}

/* Possibly negotiate authentication with the client.  Use "leader" to
 * start off the auth if necessary.
 *
 * Return NULL if authentication failed.  Return "" if anonymous access.
 * Otherwise return username.
 */
char *auth_server(int f_in, int f_out, int module, char *host, char *addr,
		  char *leader)
{
	char *users = lp_auth_users(module);
	char challenge[MD4_SUM_LENGTH*2];
	char line[BIGPATHBUFLEN];
	char secret[512];
	char pass2[MD4_SUM_LENGTH*2];
	char *tok, *pass;

	/* if no auth list then allow anyone in! */
	if (!users || !*users)
		return "";

	gen_challenge(addr, challenge);

	io_printf(f_out, "%s%s\n", leader, challenge);

	if (!read_line(f_in, line, sizeof line - 1)
	 || (pass = strchr(line, ' ')) == NULL) {
		rprintf(FLOG, "auth failed on module %s from %s (%s): "
			"invalid challenge response\n",
			lp_name(module), host, addr);
		return NULL;
	}
	*pass++ = '\0';

	if (!(users = strdup(users)))
		out_of_memory("auth_server");

	for (tok = strtok(users, " ,\t"); tok; tok = strtok(NULL, " ,\t")) {
		if (wildmatch(tok, line))
			break;
	}
	free(users);

	if (!tok) {
		rprintf(FLOG, "auth failed on module %s from %s (%s): "
			"unauthorized user\n",
			lp_name(module), host, addr);
		return NULL;
	}

	memset(secret, 0, sizeof secret);
	if (!get_secret(module, line, secret, sizeof secret - 1)) {
		memset(secret, 0, sizeof secret);
		rprintf(FLOG, "auth failed on module %s from %s (%s): "
			"missing secret for user \"%s\"\n",
			lp_name(module), host, addr, line);
		return NULL;
	}

	generate_hash(secret, challenge, pass2);
	memset(secret, 0, sizeof secret);

	if (strcmp(pass, pass2) != 0) {
		rprintf(FLOG, "auth failed on module %s from %s (%s): "
			"password mismatch\n",
			lp_name(module), host, addr);
		return NULL;
	}

	return strdup(line);
}


void auth_client(int fd, char *user, char *challenge)
{
	char *pass;
	char pass2[MD4_SUM_LENGTH*2];

	if (!user || !*user)
		user = "nobody";

	if (!(pass = getpassf(password_file))
	 && !(pass = getenv("RSYNC_PASSWORD"))) {
		/* XXX: cyeoh says that getpass is deprecated, because
		 * it may return a truncated password on some systems,
		 * and it is not in the LSB.
                 *
                 * Andrew Klein says that getpassphrase() is present
                 * on Solaris and reads up to 256 characters.
                 *
                 * OpenBSD has a readpassphrase() that might be more suitable.
                 */
		pass = getpass("Password: ");
	}

	if (!pass)
		pass = "";

	generate_hash(pass, challenge, pass2);
	io_printf(fd, "%s %s\n", user, pass2);
}
