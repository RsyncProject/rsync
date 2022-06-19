## NAME

rsync-ssl - a helper script for connecting to an ssl rsync daemon

## SYNOPSIS

```
rsync-ssl [--type=SSL_TYPE] RSYNC_ARGS
```

The online version of this manpage (that includes cross-linking of topics)
is available at <https://download.samba.org/pub/rsync/rsync-ssl.1>.

## DESCRIPTION

The rsync-ssl script helps you to run an rsync copy to/from an rsync daemon
that requires ssl connections.

The script requires that you specify an rsync-daemon arg in the style of either
`hostname::` (with 2 colons) or `rsync://hostname/`.  The default port used for
connecting is 874 (one higher than the normal 873) unless overridden in the
environment.  You can specify an overriding port via `--port` or by including
it in the normal spot in the URL format, though both of those require your
rsync version to be at least 3.2.0.

## OPTIONS

If the **first** arg is a `--type=SSL_TYPE` option, the script will only use
that particular program to open an ssl connection instead of trying to find an
openssl or stunnel executable via a simple heuristic (assuming that the
`RSYNC_SSL_TYPE` environment variable is not set as well -- see below).  This
option must specify one of `openssl` or `stunnel`.  The equal sign is
required for this particular option.

All the other options are passed through to the rsync command, so consult the
**rsync**(1) manpage for more information on how it works.

## ENVIRONMENT VARIABLES

The ssl helper scripts are affected by the following environment variables:

0.  `RSYNC_SSL_TYPE`

    Specifies the program type that should be used to open the ssl connection.
    It must be one of `openssl` or `stunnel`.  The `--type=SSL_TYPE` option
    overrides this, when specified.

0.  `RSYNC_SSL_PORT`

    If specified, the value is the port number that is used as the default when
    the user does not specify a port in their rsync command.  When not
    specified, the default port number is 874.  (Note that older rsync versions
    (prior to 3.2.0) did not communicate an overriding port number value to the
    helper script.)

0.  `RSYNC_SSL_CERT`

    If specified, the value is a filename that contains a certificate to use
    for the connection.

0.  `RSYNC_SSL_KEY`

    If specified, the value is a filename that contains a key for the provided
    certificate to use for the connection.

0.  `RSYNC_SSL_CA_CERT`

    If specified, the value is a filename that contains a certificate authority
    certificate that is used to validate the connection.

0.  `RSYNC_SSL_OPENSSL`

    Specifies the openssl executable to run when the connection type is set to
    openssl.  If unspecified, the $PATH is searched for "openssl".

0.  `RSYNC_SSL_GNUTLS`

    Specifies the gnutls-cli executable to run when the connection type is set
    to gnutls.  If unspecified, the $PATH is searched for "gnutls-cli".

0.  `RSYNC_SSL_STUNNEL`

    Specifies the stunnel executable to run when the connection type is set to
    stunnel.  If unspecified, the $PATH is searched first for "stunnel4" and
    then for "stunnel".

## EXAMPLES

>     rsync-ssl -aiv example.com::mod/ dest

>     rsync-ssl --type=openssl -aiv example.com::mod/ dest

>     rsync-ssl -aiv --port 9874 example.com::mod/ dest

>     rsync-ssl -aiv rsync://example.com:9874/mod/ dest

## THE SERVER SIDE

For help setting up an SSL/TLS supporting rsync, see the [instructions in
rsyncd.conf](rsyncd.conf.5#SSL_TLS_Daemon_Setup).

## SEE ALSO

[**rsync**(1)](rsync.1), [**rsyncd.conf**(5)](rsyncd.conf.5)

## CAVEATS

Note that using an stunnel connection requires at least version 4 of stunnel,
which should be the case on modern systems.  Also, it does not verify a
connection against the CA certificate collection, so it only encrypts the
connection without any cert validation unless you have specified the
certificate environment options.

This script also supports a `--type=gnutls` option, but at the time of this
release the gnutls-cli command was dropping output, making it unusable.  If
that bug has been fixed in your version, feel free to put gnutls into an
exported RSYNC_SSL_TYPE environment variable to make its use the default.

## BUGS

Please report bugs! See the web site at <https://rsync.samba.org/>.

## VERSION

This manpage is current for version @VERSION@ of rsync.

## CREDITS

Rsync is distributed under the GNU General Public License.  See the file
[COPYING](COPYING) for details.

A web site is available at <https://rsync.samba.org/>.  The site includes an
FAQ-O-Matic which may cover questions unanswered by this manual page.

## AUTHOR

This manpage was written by Wayne Davison.

Mailing lists for support and development are available at
<https://lists.samba.org/>.
