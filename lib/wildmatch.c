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

#define FALSE 0
#define TRUE 1
#define ABORT_ALL -1
#define ABORT_TO_STARSTAR -2

#ifdef WILD_TEST_ITERATIONS
int wildmatch_iteration_count;
#endif

static int domatch(const char *p, const char *text)
{
    int matched, special;
    char ch, prev;

#ifdef WILD_TEST_ITERATIONS
    wildmatch_iteration_count++;
#endif

    for ( ; (ch = *p) != '\0'; text++, p++) {
	if (*text == '\0' && ch != '*')
	    return FALSE;
	switch (ch) {
	  case '\\':
	    /* Literal match with following character.  Note that the test
	     * in "default" handles the p[1] == '\0' failure case. */
	    ch = *++p;
	    /* FALLTHROUGH */
	  default:
	    if (*text != ch)
		return FALSE;
	    continue;
	  case '?':
	    /* Match anything but '/'. */
	    if (*text == '/')
		return FALSE;
	    continue;
	  case '*':
	    if (*++p == '*') {
		while (*++p == '*') {}
		special = TRUE;
	    }
	    else
		special = FALSE;
	    if (*p == '\0') {
		/* Trailing "**" matches everything.  Trailing "*" matches
		 * only if there are no more slash characters. */
		return special? TRUE : strchr(text, '/') == NULL;
	    }
	    for ( ; *text; text++) {
		if ((matched = domatch(p, text)) != FALSE) {
		    if (!special || matched != ABORT_TO_STARSTAR)
			return matched;
		}
		else if (!special && *text == '/')
		    return ABORT_TO_STARSTAR;
	    }
	    return ABORT_ALL;
	  case '[':
	    ch = *++p;
	    /* Assign literal TRUE/FALSE because of "matched" comparison. */
	    special = ch == NEGATE_CLASS? TRUE : FALSE;
	    if (special) {
		/* Inverted character class. */
		ch = *++p;
	    }
	    prev = 0;
	    matched = FALSE;
	    do {
		if (!ch)
		    return FALSE;
		if (ch == '-' && prev && p[1] && p[1] != ']') {
		    if (*text <= *++p && *text >= prev)
			matched = TRUE;
		    ch = 0; /* This makes "prev" get set to 0. */
		}
		else if (*text == ch)
		    matched = TRUE;
	    } while (prev = ch, (ch = *++p) != ']');
	    if (matched == special)
		return FALSE;
	    continue;
	}
    }

    return *text == '\0';
}

int wildmatch(const char *p, const char *text)
{
#ifdef WILD_TEST_ITERATIONS
    wildmatch_iteration_count = 0;
#endif
    return domatch(p, text) == TRUE;
}
