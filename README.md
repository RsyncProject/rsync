WHAT IS RSYNC?
--------------

Rsync is a fast and extraordinarily versatile file copying tool for
both remote and local files.

Rsync uses a delta-transfer algorithm which provides a very fast method
for bringing remote files into sync.  It does this by sending just the
differences in the files across the link, without requiring that both
sets of files are present at one of the ends of the link beforehand.  At
first glance this may seem impossible because the calculation of diffs
between two files normally requires local access to both files.

A technical report describing the rsync algorithm is included with this
package.


USAGE
-----

Basically you use rsync just like scp, but rsync has many additional
options.  To get a complete list of supported options type:

    rsync --help

See the manpage for more detailed information.


SETUP
-----

Rsync normally uses ssh or rsh for communication with remote systems.
It does not need to be setuid and requires no special privileges for
installation.  You must, however, have a working ssh or rsh system.
Using ssh is recommended for its security features.

Alternatively, rsync can run in `daemon' mode, listening on a socket.
This is generally used for public file distribution, although
authentication and access control are available.

To install rsync, first run the "configure" script.  This will create a
Makefile and config.h appropriate for your system.  Then type "make".

Note that on some systems you will have to force configure not to use
gcc because gcc may not support some features (such as 64 bit file
offsets) that your system may support.  Set the environment variable CC
to the name of your native compiler before running configure in this
case.

Once built put a copy of rsync in your search path on the local and
remote systems (or use "make install").  That's it!


RSYNC DAEMONS
-------------

Rsync can also talk to "rsync daemons" which can provide anonymous or
authenticated rsync.  See the rsyncd.conf(5) man page for details on how
to setup an rsync daemon.  See the rsync(1) man page for info on how to
connect to an rsync daemon.


WEB SITE
--------

The main rsync web site is here:

    http://rsync.samba.org/

You'll find a FAQ list, downloads, resources, HTML versions of the
manpages, etc.


MAILING LISTS
-------------

There is a mailing list for the discussion of rsync and its applications
that is open to anyone to join.  New releases are announced on this
list, and there is also an announcement-only mailing list for those that
want official announcements.  See the mailing-list page for full
details:

    http://rsync.samba.org/lists.html


BUG REPORTS
-----------

To visit this web page for full the details on bug reporting:

    http://rsync.samba.org/bugzilla.html

That page contains links to the current bug list, and information on how
to report a bug well.  You might also like to try searching the Internet
for the error message you've received, or looking in the mailing list
archives at:

    http://mail-archive.com/rsync@lists.samba.org/

To send a bug report, follow the instructions on the bug-tracking
page of the web site.

Alternately, email your bug report to rsync@lists.samba.org .


GIT REPOSITORY
--------------

If you want to get the very latest version of rsync direct from the
source code repository then you can use git:

    git clone git://git.samba.org/rsync.git

See the download page for full details on all the ways to grab the
source, including nightly tar files, web-browsing of the git repository,
etc.:

    http://rsync.samba.org/download.html


COPYRIGHT
---------

Rsync was originally written by Andrew Tridgell and is currently
maintained by Wayne Davison.   It has been improved by many developers
from around the world.

Rsync may be used, modified and redistributed only under the terms of
the GNU General Public License, found in the file COPYING in this
distribution, or at:

    http://www.fsf.org/licenses/gpl.html


AVAILABILITY
------------

The main web site for rsync is http://rsync.samba.org/
The main ftp site is ftp://rsync.samba.org/pub/rsync/
This is also available as rsync://rsync.samba.org/rsyncftp/
