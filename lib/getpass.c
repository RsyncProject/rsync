/*
 * An implementation of getpass for systems that lack one.
 *
 * Copyright (C) 2013 Roman Donchenko
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

#include <stdio.h>
#include <string.h>
#include <termios.h>

#include "rsync.h"

char *getpass(const char *prompt)
{
	static char password[256];

	BOOL tty_changed = False, read_success;
	struct termios tty_old, tty_new;
	FILE *in = stdin, *out = stderr;
	FILE *tty = fopen("/dev/tty", "w+");

	if (tty)
		in = out = tty;

	if (tcgetattr(fileno(in), &tty_old) == 0) {
		tty_new = tty_old;
		tty_new.c_lflag &= ~(ECHO | ISIG);

		if (tcsetattr(fileno(in), TCSAFLUSH, &tty_new) == 0)
			tty_changed = True;
	}

	if (!tty_changed)
		fputs("(WARNING: will be visible) ", out);
	fputs(prompt, out);
	fflush(out);

	read_success = fgets(password, sizeof password, in) != NULL;

	/* Print the newline that hasn't been echoed. */
	fputc('\n', out);

	if (tty_changed)
		tcsetattr(fileno(in), TCSAFLUSH, &tty_old);

	if (tty)
		fclose(tty);

	if (read_success) {
		/* Remove the trailing newline. */
		size_t password_len = strlen(password);
		if (password_len && password[password_len - 1] == '\n')
			password[password_len - 1] = '\0';

		return password;
	}

	return NULL;
}
