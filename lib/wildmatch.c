/*
**  Do shell-style pattern matching for ?, \, [], and * characters.
**  It is 8bit clean.
**
**  Written by Rich $alz, mirror!rs, Wed Nov 26 19:03:17 EST 1986.
**  Rich $alz is now <rsalz@bbn.com>.
**
**  Modified by Wayne Davison to special-case '/' matching, to make '**'
**  work differently than '*', and to fix the character-class code.
*/

#include "rsync.h"

/* What character marks an inverted character class? */
#define NEGATE_CLASS '!'

#define false 0
#define true 1

/* Look for pattern "p" in the "text" string. */
int
wildmatch(const char *p, const char *text)
{
    int matched, special;
    char ch, prev;

    for ( ; (ch = *p) != '\0'; text++, p++) {
	if (*text == '\0' && ch != '*')
	    return false;
	switch (ch) {
	  case '\\':
	    /* Literal match with following character.  Note that the test
	     * in "default" handles the p[1] == '\0' failure case. */
	    ch = *++p;
	    /* FALLTHROUGH */
	  default:
	    if (*text != ch)
		return false;
	    continue;
	  case '?':
	    /* Match anything but '/'. */
	    if (*text == '/')
		return false;
	    continue;
	  case '*':
	    if (*++p == '*') {
		while (*++p == '*') {}
		special = true;
	    }
	    else
		special = false;
	    if (*p == '\0') {
		/* Trailing "**" matches everything. */
		return special? true : strchr(text, '/') == 0;
	    }
	    for ( ; *text; text++) {
		if (wildmatch(p, text))
		    return true;
		if (!special && *text == '/')
		    return false;
	    }
	    return false;
	  case '[':
	    special = *++p == NEGATE_CLASS ? true : false;
	    if (special) {
		/* Inverted character class. */
		p++;
	    }
	    prev = 0;
	    matched = false;
	    ch = *p;
	    if (ch == ']' || ch == '-') {
		if (*text == ch)
		    matched = true;
		prev = ch;
		ch = *++p;
	    }
	    for ( ; ch != ']'; prev = ch, ch = *++p) {
		if (!ch)
		    return false;
		if (ch == '-' && prev && p[1] && p[1] != ']') {
		    if (*text <= *++p && *text >= prev)
			matched = true;
		    ch = 0; /* This makes "prev" get set to 0. */
		}
		else if (*text == ch)
		    matched = true;
	    }
	    if (matched == special)
		return false;
	    continue;
	}
    }

    return *text == '\0';
}
