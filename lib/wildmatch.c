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
#define NEGATE_CLASS	'!'
#define NEGATE_CLASS2	'^'

#define FALSE 0
#define TRUE 1
#define ABORT_ALL -1
#define ABORT_TO_STARSTAR -2

#define CC_EQ(class, len, litmatch) ((len) == sizeof (litmatch)-1 \
				    && *(class) == *(litmatch) \
				    && strncmp((char*)class, litmatch, len) == 0)

#if defined STDC_HEADERS || !defined isascii
# define ISASCII(c) 1
#else
# define ISASCII(c) isascii(c)
#endif

#ifdef isblank
# define ISBLANK(c) (ISASCII(c) && isblank(c))
#else
# define ISBLANK(c) ((c) == ' ' || (c) == '\t')
#endif

#ifdef isgraph
# define ISGRAPH(c) (ISASCII(c) && isgraph(c))
#else
# define ISGRAPH(c) (ISASCII(c) && isprint(c) && !isspace(c))
#endif

#define ISPRINT(c) (ISASCII(c) && isprint(c))
#define ISDIGIT(c) (ISASCII(c) && isdigit(c))
#define ISALNUM(c) (ISASCII(c) && isalnum(c))
#define ISALPHA(c) (ISASCII(c) && isalpha(c))
#define ISCNTRL(c) (ISASCII(c) && iscntrl(c))
#define ISLOWER(c) (ISASCII(c) && islower(c))
#define ISPUNCT(c) (ISASCII(c) && ispunct(c))
#define ISSPACE(c) (ISASCII(c) && isspace(c))
#define ISUPPER(c) (ISASCII(c) && isupper(c))
#define ISXDIGIT(c) (ISASCII(c) && isxdigit(c))

#ifdef WILD_TEST_ITERATIONS
int wildmatch_iteration_count;
#endif

static int domatch(const unsigned char *p, const unsigned char *text)
{
    int matched, special;
    unsigned char ch, prev;

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
		return special? TRUE : strchr((char*)text, '/') == NULL;
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
#ifdef NEGATE_CLASS2
	    if (ch == NEGATE_CLASS2)
		ch = NEGATE_CLASS;
#endif
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
		    return ABORT_ALL;
		if (ch == '\\') {
		    ch = *++p;
		    if (!ch)
			return ABORT_ALL;
		    if (*text == ch)
			matched = TRUE;
		}
		else if (ch == '-' && prev && p[1] && p[1] != ']') {
		    ch = *++p;
		    if (ch == '\\') {
			ch = *++p;
			if (!ch)
			    return ABORT_ALL;
		    }
		    if (*text <= ch && *text >= prev)
			matched = TRUE;
		    ch = 0; /* This makes "prev" get set to 0. */
		}
		else if (ch == '[' && p[1] == ':') {
		    const unsigned char *s = p += 2;
		    int i;
		    while ((ch = *p) && ch != ']') p++;
		    if (!ch)
			return ABORT_ALL;
		    i = p - s - 1;
		    if (i < 0 || p[-1] != ':') {
			/* Didn't find ":]", so treat like a normal set. */
			p = s - 2;
			ch = '[';
			if (*text == ch)
			    matched = TRUE;
			continue;
		    }
		    if (CC_EQ(s,i, "alnum")) {
			if (ISALNUM(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "alpha")) {
			if (ISALPHA(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "blank")) {
			if (ISBLANK(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "cntrl")) {
			if (ISCNTRL(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "digit")) {
			if (ISDIGIT(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "graph")) {
			if (ISGRAPH(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "lower")) {
			if (ISLOWER(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "print")) {
			if (ISPRINT(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "punct")) {
			if (ISPUNCT(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "space")) {
			if (ISSPACE(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "upper")) {
			if (ISUPPER(*text))
			    matched = TRUE;
		    }
		    else if (CC_EQ(s,i, "xdigit")) {
			if (ISXDIGIT(*text))
			    matched = TRUE;
		    }
		    else /* malformed [:class:] string */
			return ABORT_ALL;
		    ch = 0; /* This makes "prev" get set to 0. */
		}
		else if (*text == ch)
		    matched = TRUE;
	    } while (prev = ch, (ch = *++p) != ']');
	    if (matched == special || *text == '/')
		return FALSE;
	    continue;
	}
    }

    return *text == '\0';
}

/* Find the pattern (p) in the text string (t). */
int wildmatch(const char *p, const char *t)
{
#ifdef WILD_TEST_ITERATIONS
    wildmatch_iteration_count = 0;
#endif
    return domatch((const unsigned char*)p, (const unsigned char*)t) == TRUE;
}
