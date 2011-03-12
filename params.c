/* This modules is based on the params.c module from Samba, written by Karl Auer
   and much modifed by Christopher Hertel. */

/*
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

/* -------------------------------------------------------------------------- **
 *
 * Module name: params
 *
 * -------------------------------------------------------------------------- **
 *
 *  This module performs lexical analysis and initial parsing of a
 *  Windows-like parameter file.  It recognizes and handles four token
 *  types:  section-name, parameter-name, parameter-value, and
 *  end-of-file.  Comments and line continuation are handled
 *  internally.
 *
 *  The entry point to the module is function pm_process().  This
 *  function opens the source file, calls the Parse() function to parse
 *  the input, and then closes the file when either the EOF is reached
 *  or a fatal error is encountered.
 *
 *  A sample parameter file might look like this:
 *
 *  [section one]
 *  parameter one = value string
 *  parameter two = another value
 *  [section two]
 *  new parameter = some value or t'other
 *
 *  The parameter file is divided into sections by section headers:
 *  section names enclosed in square brackets (eg. [section one]).
 *  Each section contains parameter lines, each of which consist of a
 *  parameter name and value delimited by an equal sign.  Roughly, the
 *  syntax is:
 *
 *    <file>            :==  { <section> } EOF
 *
 *    <section>         :==  <section header> { <parameter line> }
 *
 *    <section header>  :==  '[' NAME ']'
 *
 *    <parameter line>  :==  NAME '=' VALUE '\n'
 *
 *  Blank lines and comment lines are ignored.  Comment lines are lines
 *  beginning with either a semicolon (';') or a pound sign ('#').
 *
 *  All whitespace in section names and parameter names is compressed
 *  to single spaces.  Leading and trailing whitespace is stipped from
 *  both names and values.
 *
 *  Only the first equals sign in a parameter line is significant.
 *  Parameter values may contain equals signs, square brackets and
 *  semicolons.  Internal whitespace is retained in parameter values,
 *  with the exception of the '\r' character, which is stripped for
 *  historic reasons.  Parameter names may not start with a left square
 *  bracket, an equal sign, a pound sign, or a semicolon, because these
 *  are used to identify other tokens.
 *
 * -------------------------------------------------------------------------- **
 */

#include "rsync.h"
#include "ifuncs.h"
#include "itypes.h"

/* -------------------------------------------------------------------------- **
 * Constants...
 */

#define BUFR_INC 1024


/* -------------------------------------------------------------------------- **
 * Variables...
 *
 *  bufr        - pointer to a global buffer.  This is probably a kludge,
 *                but it was the nicest kludge I could think of (for now).
 *  bSize       - The size of the global buffer <bufr>.
 */

static char *bufr  = NULL;
static int   bSize = 0;
static BOOL  (*the_sfunc)(char *);
static BOOL  (*the_pfunc)(char *, char *);

/* -------------------------------------------------------------------------- **
 * Functions...
 */

static int EatWhitespace( FILE *InFile )
  /* ------------------------------------------------------------------------ **
   * Scan past whitespace (see ctype(3C)) and return the first non-whitespace
   * character, or newline, or EOF.
   *
   *  Input:  InFile  - Input source.
   *
   *  Output: The next non-whitespace character in the input stream.
   *
   *  Notes:  Because the config files use a line-oriented grammar, we
   *          explicitly exclude the newline character from the list of
   *          whitespace characters.
   *        - Note that both EOF (-1) and the nul character ('\0') are
   *          considered end-of-file markers.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  int c;

  for( c = getc( InFile ); isspace( c ) && ('\n' != c); c = getc( InFile ) )
    ;
  return( c );
  } /* EatWhitespace */

static int EatComment( FILE *InFile )
  /* ------------------------------------------------------------------------ **
   * Scan to the end of a comment.
   *
   *  Input:  InFile  - Input source.
   *
   *  Output: The character that marks the end of the comment.  Normally,
   *          this will be a newline, but it *might* be an EOF.
   *
   *  Notes:  Because the config files use a line-oriented grammar, we
   *          explicitly exclude the newline character from the list of
   *          whitespace characters.
   *        - Note that both EOF (-1) and the nul character ('\0') are
   *          considered end-of-file markers.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  int c;

  for( c = getc( InFile ); ('\n'!=c) && (EOF!=c) && (c>0); c = getc( InFile ) )
    ;
  return( c );
  } /* EatComment */

static int Continuation( char *line, int pos )
  /* ------------------------------------------------------------------------ **
   * Scan backards within a string to discover if the last non-whitespace
   * character is a line-continuation character ('\\').
   *
   *  Input:  line  - A pointer to a buffer containing the string to be
   *                  scanned.
   *          pos   - This is taken to be the offset of the end of the
   *                  string.  This position is *not* scanned.
   *
   *  Output: The offset of the '\\' character if it was found, or -1 to
   *          indicate that it was not.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  pos--;
  while( pos >= 0 && isSpace(line + pos) )
     pos--;

  return( ((pos >= 0) && ('\\' == line[pos])) ? pos : -1 );
  } /* Continuation */


static BOOL Section( FILE *InFile, BOOL (*sfunc)(char *) )
  /* ------------------------------------------------------------------------ **
   * Scan a section name, and pass the name to function sfunc().
   *
   *  Input:  InFile  - Input source.
   *          sfunc   - Pointer to the function to be called if the section
   *                    name is successfully read.
   *
   *  Output: True if the section name was read and True was returned from
   *          <sfunc>.  False if <sfunc> failed or if a lexical error was
   *          encountered.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  int   c;
  int   i;
  int   end;
  char *func  = "params.c:Section() -";

  i = 0;      /* <i> is the offset of the next free byte in bufr[] and  */
  end = 0;    /* <end> is the current "end of string" offset.  In most  */
              /* cases these will be the same, but if the last          */
              /* character written to bufr[] is a space, then <end>     */
              /* will be one less than <i>.                             */

  c = EatWhitespace( InFile );    /* We've already got the '['.  Scan */
                                  /* past initial white space.        */

  while( (EOF != c) && (c > 0) )
    {

    /* Check that the buffer is big enough for the next character. */
    if( i > (bSize - 2) )
      {
      bSize += BUFR_INC;
      bufr   = realloc_array( bufr, char, bSize );
      if( NULL == bufr )
        {
        rprintf(FLOG, "%s Memory re-allocation failure.", func);
        return( False );
        }
      }

    /* Handle a single character. */
    switch( c )
      {
      case ']':                       /* Found the closing bracket.         */
        bufr[end] = '\0';
        if( 0 == end )                  /* Don't allow an empty name.       */
          {
          rprintf(FLOG, "%s Empty section name in config file.\n", func );
          return( False );
          }
        if( !sfunc( bufr ) )            /* Got a valid name.  Deal with it. */
          return( False );
        (void)EatComment( InFile );     /* Finish off the line.             */
        return( True );

      case '\n':                      /* Got newline before closing ']'.    */
        i = Continuation( bufr, i );    /* Check for line continuation.     */
        if( i < 0 )
          {
          bufr[end] = '\0';
          rprintf(FLOG, "%s Badly formed line in config file: %s\n",
                   func, bufr );
          return( False );
          }
        end = ( (i > 0) && (' ' == bufr[i - 1]) ) ? (i - 1) : (i);
        c = getc( InFile );             /* Continue with next line.         */
        break;

      default:                        /* All else are a valid name chars.   */
        if( isspace( c ) )              /* One space per whitespace region. */
          {
          bufr[end] = ' ';
          i = end + 1;
          c = EatWhitespace( InFile );
          }
        else                            /* All others copy verbatim.        */
          {
          bufr[i++] = c;
          end = i;
          c = getc( InFile );
          }
      }
    }

  /* We arrive here if we've met the EOF before the closing bracket. */
  rprintf(FLOG, "%s Unexpected EOF in the config file: %s\n", func, bufr );
  return( False );
  } /* Section */

static BOOL Parameter( FILE *InFile, BOOL (*pfunc)(char *, char *), int c )
  /* ------------------------------------------------------------------------ **
   * Scan a parameter name and value, and pass these two fields to pfunc().
   *
   *  Input:  InFile  - The input source.
   *          pfunc   - A pointer to the function that will be called to
   *                    process the parameter, once it has been scanned.
   *          c       - The first character of the parameter name, which
   *                    would have been read by Parse().  Unlike a comment
   *                    line or a section header, there is no lead-in
   *                    character that can be discarded.
   *
   *  Output: True if the parameter name and value were scanned and processed
   *          successfully, else False.
   *
   *  Notes:  This function is in two parts.  The first loop scans the
   *          parameter name.  Internal whitespace is compressed, and an
   *          equal sign (=) terminates the token.  Leading and trailing
   *          whitespace is discarded.  The second loop scans the parameter
   *          value.  When both have been successfully identified, they are
   *          passed to pfunc() for processing.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  int   i       = 0;    /* Position within bufr. */
  int   end     = 0;    /* bufr[end] is current end-of-string. */
  int   vstart  = 0;    /* Starting position of the parameter value. */
  char *func    = "params.c:Parameter() -";

  /* Read the parameter name. */
  while( 0 == vstart )  /* Loop until we've found the start of the value. */
    {

    if( i > (bSize - 2) )       /* Ensure there's space for next char.    */
      {
      bSize += BUFR_INC;
      bufr   = realloc_array( bufr, char, bSize );
      if( NULL == bufr )
        {
        rprintf(FLOG, "%s Memory re-allocation failure.", func) ;
        return( False );
        }
      }

    switch( c )
      {
      case '=':                 /* Equal sign marks end of param name. */
        if( 0 == end )              /* Don't allow an empty name.      */
          {
          rprintf(FLOG, "%s Invalid parameter name in config file.\n", func );
          return( False );
          }
        bufr[end++] = '\0';         /* Mark end of string & advance.   */
        i = vstart = end;           /* New string starts here.         */
        c = EatWhitespace(InFile);
        break;

      case '\n':                /* Find continuation char, else error. */
        i = Continuation( bufr, i );
        if( i < 0 )
          {
          bufr[end] = '\0';
          rprintf(FLOG, "%s Ignoring badly formed line in config file: %s\n",
                   func, bufr );
          return( True );
          }
        end = ( (i > 0) && (' ' == bufr[i - 1]) ) ? (i - 1) : (i);
        c = getc( InFile );       /* Read past eoln.                   */
        break;

      case '\0':                /* Shouldn't have EOF within param name. */
      case EOF:
        bufr[i] = '\0';
        rprintf(FLOG, "%s Unexpected end-of-file at: %s\n", func, bufr );
        return( True );

      case ' ':
      case '\t':
        /* A directive divides at the first space or tab. */
        if (*bufr == '&') {
          bufr[end++] = '\0';
          i = vstart = end;
          c = EatWhitespace(InFile);
          if (c == '=')
            c = EatWhitespace(InFile);
          break;
        }
        /* FALL THROUGH */

      default:
        if( isspace( c ) )     /* One ' ' per whitespace region.       */
          {
          bufr[end] = ' ';
          i = end + 1;
          c = EatWhitespace( InFile );
          }
        else                   /* All others verbatim.                 */
          {
          bufr[i++] = c;
          end = i;
          c = getc( InFile );
          }
      }
    }

  /* Now parse the value. */
  while( (EOF !=c) && (c > 0) )
    {

    if( i > (bSize - 2) )       /* Make sure there's enough room. */
      {
      bSize += BUFR_INC;
      bufr   = realloc_array( bufr, char, bSize );
      if( NULL == bufr )
        {
        rprintf(FLOG, "%s Memory re-allocation failure.", func) ;
        return( False );
        }
      }

    switch( c )
      {
      case '\r':              /* Explicitly remove '\r' because the older */
        c = getc( InFile );   /* version called fgets_slash() which also  */
        break;                /* removes them.                            */

      case '\n':              /* Marks end of value unless there's a '\'. */
        i = Continuation( bufr, i );
        if( i < 0 )
          c = 0;
        else
          {
          for( end = i; end >= 0 && isSpace(bufr + end); end-- )
            ;
          c = getc( InFile );
          }
        break;

      default:               /* All others verbatim.  Note that spaces do */
        bufr[i++] = c;       /* not advance <end>.  This allows trimming  */
        if( !isspace( c ) )  /* of whitespace at the end of the line.     */
          end = i;
        c = getc( InFile );
        break;
      }
    }
  bufr[end] = '\0';          /* End of value. */

  return( pfunc( bufr, &bufr[vstart] ) );   /* Pass name & value to pfunc().  */
  } /* Parameter */

static int name_cmp(const void *n1, const void *n2)
{
    return strcmp(*(char * const *)n1, *(char * const *)n2);
}

static int include_config(char *include, int manage_globals)
{
    STRUCT_STAT sb;
    char *match = manage_globals ? "*.conf" : "*.inc";
    int ret;

    if (do_stat(include, &sb) < 0) {
	rsyserr(FLOG, errno, "unable to stat config file \"%s\"", include);
	return 0;
    }

    if (S_ISREG(sb.st_mode)) {
	if (manage_globals && the_sfunc)
	    the_sfunc("]push");
	ret = pm_process(include, the_sfunc, the_pfunc);
	if (manage_globals && the_sfunc)
	    the_sfunc("]pop");
    } else if (S_ISDIR(sb.st_mode)) {
	char buf[MAXPATHLEN], **bpp;
	item_list conf_list;
	struct dirent *di;
	size_t j;
	DIR *d;

	if (!(d = opendir(include))) {
	    rsyserr(FLOG, errno, "unable to open config dir \"%s\"", include);
	    return 0;
	}

	memset(&conf_list, 0, sizeof conf_list);

	while ((di = readdir(d)) != NULL) {
	    char *dname = d_name(di);
	    if (!wildmatch(match, dname))
		continue;
	    bpp = EXPAND_ITEM_LIST(&conf_list, char *, 32);
	    pathjoin(buf, sizeof buf, include, dname);
	    *bpp = strdup(buf);
	}
	closedir(d);

	if (!(bpp = conf_list.items))
	    return 1;

	if (conf_list.count > 1)
	    qsort(bpp, conf_list.count, sizeof (char *), name_cmp);

	for (j = 0, ret = 1; j < conf_list.count; j++) {
	    if (manage_globals && the_sfunc)
		the_sfunc(j == 0 ? "]push" : "]reset");
	    if ((ret = pm_process(bpp[j], the_sfunc, the_pfunc)) != 1)
		break;
	}

	if (manage_globals && the_sfunc)
	    the_sfunc("]pop");

	for (j = 0; j < conf_list.count; j++)
	    free(bpp[j]);
	free(bpp);
    } else
	ret = 0;

    return ret;
}

static int parse_directives(char *name, char *val)
{
    if (strcasecmp(name, "&include") == 0)
        return include_config(val, 1);
    if (strcasecmp(name, "&merge") == 0)
        return include_config(val, 0);
    rprintf(FLOG, "Unknown directive: %s.\n", name);
    return 0;
}

static int Parse( FILE *InFile,
                   BOOL (*sfunc)(char *),
                   BOOL (*pfunc)(char *, char *) )
  /* ------------------------------------------------------------------------ **
   * Scan & parse the input.
   *
   *  Input:  InFile  - Input source.
   *          sfunc   - Function to be called when a section name is scanned.
   *                    See Section().
   *          pfunc   - Function to be called when a parameter is scanned.
   *                    See Parameter().
   *
   *  Output: 1 if the file was successfully scanned, 2 if the file was
   *  scanned until a section header with no section function, else 0.
   *
   *  Notes:  The input can be viewed in terms of 'lines'.  There are four
   *          types of lines:
   *            Blank      - May contain whitespace, otherwise empty.
   *            Comment    - First non-whitespace character is a ';' or '#'.
   *                         The remainder of the line is ignored.
   *            Section    - First non-whitespace character is a '['.
   *            Parameter  - The default case.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  int    c;

  c = EatWhitespace( InFile );
  while( (EOF != c) && (c > 0) )
    {
    switch( c )
      {
      case '\n':                        /* Blank line. */
        c = EatWhitespace( InFile );
        break;

      case ';':                         /* Comment line. */
      case '#':
        c = EatComment( InFile );
        break;

      case '[':                         /* Section Header. */
        if (!sfunc)
          return 2;
        if( !Section( InFile, sfunc ) )
          return 0;
        c = EatWhitespace( InFile );
        break;

      case '\\':                        /* Bogus backslash. */
        c = EatWhitespace( InFile );
        break;

      case '&':                         /* Handle directives */
        the_sfunc = sfunc;
        the_pfunc = pfunc;
        c = Parameter( InFile, parse_directives, c );
        if (c != 1)
          return c;
        c = EatWhitespace( InFile );
        break;

      default:                          /* Parameter line. */
        if( !Parameter( InFile, pfunc, c ) )
          return 0;
        c = EatWhitespace( InFile );
        break;
      }
    }
  return 1;
  } /* Parse */

static FILE *OpenConfFile( char *FileName )
  /* ------------------------------------------------------------------------ **
   * Open a config file.
   *
   *  Input:  FileName  - The pathname of the config file to be opened.
   *
   *  Output: A pointer of type (FILE *) to the opened file, or NULL if the
   *          file could not be opened.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  FILE *OpenedFile;
  char *func = "params.c:OpenConfFile() -";

  if( NULL == FileName || 0 == *FileName )
    {
    rprintf(FLOG, "%s No config filename specified.\n", func);
    return( NULL );
    }

  OpenedFile = fopen( FileName, "r" );
  if( NULL == OpenedFile )
    {
    rsyserr(FLOG, errno, "unable to open config file \"%s\"",
	    FileName);
    }

  return( OpenedFile );
  } /* OpenConfFile */

int pm_process( char *FileName,
                 BOOL (*sfunc)(char *),
                 BOOL (*pfunc)(char *, char *) )
  /* ------------------------------------------------------------------------ **
   * Process the named parameter file.
   *
   *  Input:  FileName  - The pathname of the parameter file to be opened.
   *          sfunc     - A pointer to a function that will be called when
   *                      a section name is discovered.
   *          pfunc     - A pointer to a function that will be called when
   *                      a parameter name and value are discovered.
   *
   *  Output: 1 if the file was successfully parsed, 2 if parsing ended at a
   *  section header w/o a section function, else 0.
   *
   * ------------------------------------------------------------------------ **
   */
  {
  int   result;
  FILE *InFile;
  char *func = "params.c:pm_process() -";

  InFile = OpenConfFile( FileName );          /* Open the config file. */
  if( NULL == InFile )
    return( False );

  if( NULL != bufr )                          /* If we already have a buffer */
    result = Parse( InFile, sfunc, pfunc );   /* (recursive call), then just */
                                              /* use it.                     */

  else                                        /* If we don't have a buffer   */
    {                                         /* allocate one, then parse,   */
    bSize = BUFR_INC;                         /* then free.                  */
    bufr = new_array( char, bSize );
    if( NULL == bufr )
      {
      rprintf(FLOG, "%s memory allocation failure.\n", func);
      fclose(InFile);
      return( False );
      }
    result = Parse( InFile, sfunc, pfunc );
    free( bufr );
    bufr  = NULL;
    bSize = 0;
    }

  fclose(InFile);

  if( !result )                               /* Generic failure. */
    {
    rprintf(FLOG, "%s Failed.  Error returned from params.c:parse().\n", func);
    return 0;
    }

  return result;
  } /* pm_process */

/* -------------------------------------------------------------------------- */

