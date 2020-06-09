# NAME

rsync-ssl - a helper script for connecting to an ssl rsync daemon

# SYNOPSIS

```
rsync-ssl [--type=openssl|stunnel] RSYNC_ARGS
```

# DESCRIPTION

The rsync-ssl script helps you to run an rsync copy to/from an rsync daemon
that requires ssl connections.

If the **first** arg is a `--type=NAME` option, the script will only use that
particular program to open an ssl connection instead of trying to find an
stunnel or openssl executable via a simple heuristic (assuming that the
`RSYNC_SSL_TYPE` environment variable is not set as well -- see below).  This
option must be one of `--type=openssl` or `--type=stunnel`.  The equal sign is
required for this particular option.

All the other options are passed through to the rsync command, so consult the
**rsync** manpage for more information on how it works.

Note that the stunnel connection type requires at least version 4 of stunnel,
which should be the case on modern systems.

This script requires that a helper script named **ssl-rsh** be installed the
@LIBDIR@ dir so that rsync can use it as its remote-shell command.

# ENVIRONMENT VARIABLES

The ssl helper scripts are affected by the following environment variables:

0.  `RSYNC_SSL_TYPE` Specifies the program type that should be used to open the
    ssl connection.  It must be one of "openssl" or "stunnel".  The
    `--type=NAME` option overrides this, if specified.
0.  `RSYNC_SSL_PORT` If specified, the value is the port number that is used as
    the default when the user does not specify a port in their rsync command.
    When not specified, the default port number is 874.  (Note that older rsync
    versions (prior to 3.2.0) did not communicate an overriding port number
    value to the helper script.)
0.  `RSYNC_SSL_CERT` If specified, the value is a filename that contains a
    certificate to use for the connection.
0.  `RSYNC_SSL_CA_CERT` If specified, the value is a filename that contains a
    certificate authority certificate that is used to validate the connection.
0.  `RSYNC_SSL_STUNNEL` Specifies the stunnel executable to run when the
    connection type is set to stunnel.  If unspecified, the $PATH is searched
    first for "stunnel4" and then for "stunnel".
0.  `RSYNC_SSL_OPENSSL` Specifies the openssl executable to run when the
    connection type is set to openssl.  If unspecified, the $PATH is searched
    for "openssl".

# EXAMPLES

>     rsync-ssl -aiv example.com::src/ dest

>     rsync-ssl --type=openssl -aiv example.com::src/ dest

# FILES

@LIBDIR@/ssl-rsh

# SEE ALSO

**rsync**(1), **rsyncd.conf**(5)

# BUGS

Please report bugs! See the web site at <http://rsync.samba.org/>.

# VERSION

This man page is current for version @VERSION@ of rsync.

# CREDITS

rsync is distributed under the GNU General Public License.  See the file
COPYING for details.

A web site is available at <http://rsync.samba.org/>.  The site includes an
FAQ-O-Matic which may cover questions unanswered by this manual page.

# AUTHOR

This manpage was written by Wayne Davison.

Mailing lists for support and development are available at
<http://lists.samba.org/>.
