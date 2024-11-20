#include "system.h"
#include <stdarg.h>
#include <errno.h>
#ifdef HAVE_LANGINFO_H
#include <langinfo.h>
#endif
#include "poptint.h"

/* Any pair of 32 bit hashes can be used. lookup3.c generates pairs, will do. */
#define _JLU3_jlu32lpair        1
#define	jlu32lpair	poptJlu32lpair
#include "lookup3.c"

const char *
POPT_prev_char (const char *str)
{
    const char *p = str;

    while (1) {
	p--;
	if (((unsigned)*p & 0xc0) != (unsigned)0x80)
	    return p;
    }
}

const char *
POPT_next_char (const char *str)
{
    const char *p = str;

    while (*p != '\0') {
	p++;
	if (((unsigned)*p & 0xc0) != (unsigned)0x80)
	    break;
    }
    return p;
}

#if !defined(POPT_fprintf)	/* XXX lose all the goop ... */

#if defined(ENABLE_NLS) && defined(HAVE_LIBINTL_H) && defined(HAVE_DCGETTEXT)
/*
 * Rebind a "UTF-8" codeset for popt's internal use.
 */
char *
POPT_dgettext(const char * dom, const char * str)
{
    char * codeset = NULL;
    char * retval = NULL;

    if (!dom) 
	dom = textdomain(NULL);
    codeset = bind_textdomain_codeset(dom, NULL);
    bind_textdomain_codeset(dom, "UTF-8");
    retval = dgettext(dom, str);
    bind_textdomain_codeset(dom, codeset);

    return retval;
}
#endif

#ifdef HAVE_ICONV
/**
 * Return malloc'd string converted from UTF-8 to current locale.
 * @param istr		input string (UTF-8 encoding assumed)
 * @return		localized string
 */
static char *
strdup_locale_from_utf8 (char * istr)
{
    char * codeset = NULL;
    char * ostr = NULL;
    iconv_t cd;

    if (istr == NULL)
	return NULL;

#ifdef HAVE_LANGINFO_H
    codeset = nl_langinfo ((nl_item)CODESET);
#endif

    if (codeset != NULL && strcmp(codeset, "UTF-8") != 0
     && (cd = iconv_open(codeset, "UTF-8")) != (iconv_t)-1)
    {
	char * shift_pin = NULL;
	size_t db = strlen(istr);
	char * dstr = malloc((db + 1) * sizeof(*dstr));
	char * dstr_tmp;
	char * pin = istr;
	char * pout = dstr;
	size_t ib = db;
	size_t ob = db;
	size_t err;

	if (dstr == NULL) {
	    (void) iconv_close(cd);
	    return NULL;
	}
	err = iconv(cd, NULL, NULL, NULL, NULL);
	while (1) {
	    *pout = '\0';
	    err = iconv(cd, &pin, &ib, &pout, &ob);
	    if (err != (size_t)-1) {
		if (shift_pin == NULL) {
		    shift_pin = pin;
		    pin = NULL;
		    ib = 0;
		    continue;
		}
	    } else
	    switch (errno) {
	    case E2BIG:
	    {	size_t used = (size_t)(pout - dstr);
		db *= 2;
		dstr_tmp = realloc(dstr, (db + 1) * sizeof(*dstr));
		if (dstr_tmp == NULL) {
		    free(dstr);
		    (void) iconv_close(cd);
		    return NULL;
		}
		dstr = dstr_tmp;
		pout = dstr + used;
		ob = db - used;
		continue;
	    }   break;
	    case EINVAL:
	    case EILSEQ:
	    default:
		break;
	    }
	    break;
	}
	(void) iconv_close(cd);
	*pout = '\0';
	ostr = xstrdup(dstr);
	free(dstr);
    } else
	ostr = xstrdup(istr);

    return ostr;
}
#endif

int
POPT_fprintf (FILE * stream, const char * format, ...)
{
    char * b = NULL, * ob = NULL;
    int rc;
    va_list ap;

#if defined(HAVE_VASPRINTF)
    va_start(ap, format);
    if ((rc = vasprintf(&b, format, ap)) < 0)
	b = NULL;
    va_end(ap);
#else
    size_t nb = (size_t)1;

    /* HACK: add +1 to the realloc no. of bytes "just in case". */
    /* XXX Likely unneeded, the issues wrto vsnprintf(3) return b0rkage have
     * to do with whether the final '\0' is counted (or not). The code
     * below already adds +1 for the (possibly already counted) trailing NUL.
     */
    while ((b = realloc(b, nb+1)) != NULL) {
	va_start(ap, format);
	rc = vsnprintf(b, nb, format, ap);
	va_end(ap);
	if (rc > -1) {	/* glibc 2.1 */
	    if ((size_t)rc < nb)
		break;
	    nb = (size_t)(rc + 1);	/* precise buffer length known */
	} else 		/* glibc 2.0 */
	    nb += (nb < (size_t)100 ? (size_t)100 : nb);
	ob = b;
    }
#endif

    rc = 0;
    if (b != NULL) {
#ifdef HAVE_ICONV
	ob = strdup_locale_from_utf8(b);
	if (ob != NULL) {
	    rc = fprintf(stream, "%s", ob);
	    free(ob);
	} else
#endif
	    rc = fprintf(stream, "%s", b);
	free (b);
    }

    return rc;
}

#endif	/* !defined(POPT_fprintf) */
