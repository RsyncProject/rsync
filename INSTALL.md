# How to build and install rsync

When building rsync, you'll want to install various libraries in order to get
all the features enabled.  The configure script will alert you when the
newest libraries are missing and tell you the appropriate `--disable-LIB`
option to use if you want to just skip that feature.  What follows are various
support libraries that you may want to install to build rsync with the maximum
features (the impatient can skip down to the package summary):

## The basic setup

You need to have a C compiler installed and optionally a C++ compiler in order
to try to build some hardware-accelerated checksum routines.  Rsync also needs
a modern awk, which might be provided via gawk or nawk on some OSes.

## Autoconf & manpages

If you're installing from the git repo (instead of a release tar file) you'll
also need the GNU autotools (autoconf & automake) and your choice of 2 python3
markdown libraries: cmarkgfm or commonmark (needed to generate the manpages).
If your OS doesn't provide a python3-cmarkgfm or python3-commonmark package,
you can run the following to install the commonmark python library for your
build user (after installing python3's pip package):

>     python3 -mpip install --user commonmark

You can test if you've got it fixed by running (from the rsync checkout):

>     ./md-convert --test rsync-ssl.1.md

Alternately, you can avoid generating the manpages by fetching the very latest
versions (that match the latest git source) from the [generated-files][6] dir.
One way to do that is to run:

>     ./prepare-source fetchgen

[6]: https://download.samba.org/pub/rsync/generated-files/

## ACL support

To support copying ACL file information, make sure you have an acl
development library installed. It also helps to have the helper programs
installed to manipulate ACLs and to run the rsync testsuite.

## Xattr support

To support copying xattr file information, make sure you have an attr
development library installed. It also helps to have the helper programs
installed to manipulate xattrs and to run the rsync testsuite.

## xxhash

The [xxHash library][1] provides extremely fast checksum functions that can
make the "rsync algorithm" run much more quickly, especially when matching
blocks in large files.  Installing this development library adds xxhash
checksums as the default checksum algorithm.  You'll need at least v0.8.0
if you want rsync to include the full range of its checksum algorithms.

[1]: https://cyan4973.github.io/xxHash/

## zstd

The [zstd library][2] compression algorithm that uses less CPU than
the default zlib algorithm at the same compression level.  Note that you
need at least version 1.4, so you might need to skip the zstd compression if
you can only install a 1.3 release.  Installing this development library
adds zstd compression as the default compression algorithm.

[2]: http://facebook.github.io/zstd/

## lz4

The [lz4 library][3] compression algorithm that uses very little CPU, though
it also has the smallest compression ratio of other algorithms.  Installing
this development library adds lz4 compression as an available compression
algorithm.

[3]: https://lz4.github.io/lz4/

## openssl crypto

The [openssl crypto library][4] provides some hardware accelerated checksum
algorithms for MD4 and MD5.  Installing this development library makes rsync
use the (potentially) faster checksum routines when computing MD4 & MD5
checksums.

[4]: https://www.openssl.org/docs/man1.0.2/man3/crypto.html

## Package summary

To help you get the libraries installed, here are some package install commands
for various OSes.  The commands are split up to correspond with the above
items, but feel free to combine the package names into a single install, if you
like.

 -  For Debian and Ubuntu (Debian Buster users may want to briefly(?) enable
    buster-backports to update zstd from 1.3 to 1.4):

    >     sudo apt install -y gcc g++ gawk autoconf automake python3-cmarkgfm
    >     sudo apt install -y acl libacl1-dev
    >     sudo apt install -y attr libattr1-dev
    >     sudo apt install -y libxxhash-dev
    >     sudo apt install -y libzstd-dev
    >     sudo apt install -y liblz4-dev
    >     sudo apt install -y libssl-dev

Or run support/install_deps_ubuntu.sh

 -  For CentOS (use EPEL for python3-pip):

    >     sudo yum -y install epel-release
    >     sudo yum -y install gcc g++ gawk autoconf automake python3-pip
    >     sudo yum -y install acl libacl-devel
    >     sudo yum -y install attr libattr-devel
    >     sudo yum -y install xxhash-devel
    >     sudo yum -y install libzstd-devel
    >     sudo yum -y install lz4-devel
    >     sudo yum -y install openssl-devel
    >     python3 -mpip install --user commonmark

 -  For Fedora 33:

    >     sudo dnf -y install acl libacl-devel
    >     sudo dnf -y install attr libattr-devel
    >     sudo dnf -y install xxhash-devel
    >     sudo dnf -y install libzstd-devel
    >     sudo dnf -y install lz4-devel
    >     sudo dnf -y install openssl-devel

 -  For FreeBSD (this assumes that the python3 version is 3.7):

    >     sudo pkg install -y autotools python3 py37-CommonMark
    >     sudo pkg install -y xxhash
    >     sudo pkg install -y zstd
    >     sudo pkg install -y liblz4

 -  For macOS:

    >     brew install automake
    >     brew install xxhash
    >     brew install zstd
    >     brew install lz4
    >     brew install openssl

 -  For Cygwin (with all cygwin programs stopped, run the appropriate setup program from a cmd shell):

    >     setup-x86_64 --quiet-mode -P make,gawk,autoconf,automake,gcc-core,python38,python38-pip
    >     setup-x86_64 --quiet-mode -P attr,libattr-devel
    >     setup-x86_64 --quiet-mode -P libzstd-devel
    >     setup-x86_64 --quiet-mode -P liblz4-devel
    >     setup-x86_64 --quiet-mode -P libssl-devel

    Sometimes cygwin has commonmark packaged and sometimes it doesn't. Now that
    its python38 has stabilized, you could install python38-commonmark. Or just
    avoid the issue by running this from a bash shell as your build user:

    >     python3 -mpip install --user commonmark

## Build and install

After installing the various libraries, you need to configure, build, and
install the source:

>      ./configure
>      make
>      sudo make install

The default install path is /usr/local/bin, but you can set the installation
directory and other parameters using options to ./configure.  To see them, use:

>     ./configure --help

Configure tries to figure out if the local system uses group "nobody" or
"nogroup" by looking in the /etc/group file.  (This is only used for the
default group of an rsync daemon, which attempts to run with "nobody"
user and group permissions.)  You can change the default user and group
for the daemon by editing the NOBODY_USER and NOBODY_GROUP defines in
config.h, or just override them in your /etc/rsyncd.conf file.

As of 2.4.7, rsync uses Eric Troan's popt option-parsing library.  A
cut-down copy of a recent release is included in the rsync distribution,
and will be used if there is no popt library on your build host, or if
the `--with-included-popt` option is passed to ./configure.

If you configure using `--enable-maintainer-mode`, then rsync will try
to pop up an xterm on DISPLAY=:0 if it crashes.  You might find this
useful, but it should be turned off for production builds.

If you want to automatically use a separate "build" directory based on
the current git branch name, start with a pristine git checkout and run
"mkdir auto-build-save" before you run the first ./configure command.
That will cause a fresh build dir to spring into existence along with a
special Makefile symlink that allows you to run "make" and "./configure"
from the source dir (the "build" dir gets auto switched based on branch).
This is helpful when using the branch-from-patch and patch-update scripts
to maintain the official rsync patches.  If you ever need to build from
a "detached head" git position then you'll need to manually chdir into
the build dir to run make.  I also like to create 2 more symlinks in the
source dir:  `ln -s build/rsync . ; ln -s build/testtmp .`

## Make compatibility

Note that Makefile.in has a rule that uses a wildcard in a prerequisite.  If
your make has a problem with this rule, you will see an error like this:

    Don't know how to make ./*.c

You can change the "proto.h-tstamp" target in Makefile.in to list all the \*.c
filenames explicitly in order to avoid this issue.

## RPM notes

Under packaging you will find .spec files for several distributions.
The .spec file in packaging/lsb can be used for Linux systems that
adhere to the Linux Standards Base (e.g., RedHat and others).

## HP-UX notes

The HP-UX 10.10 "bundled" C compiler seems not to be able to cope with
ANSI C.  You may see this error message in config.log if ./configure
fails:

    (Bundled) cc: "configure", line 2162: error 1705: Function prototypes are an ANSI feature.

Install gcc or HP's "ANSI/C Compiler".

## Mac OS X notes

Some versions of Mac OS X (Darwin) seem to have an IPv6 stack, but do
not completely implement the "New Sockets" API.

[This site][5] says that Apple started to support IPv6 in 10.2 (Jaguar).  If
your build fails, try again after running configure with `--disable-ipv6`.

Apple Silicon macs may install packages in a slightly different location and require flags.
CFLAGS="-I /opt/homebrew/include" LDFLAGS="-L /opt/homebrew/lib"

[5]: http://www.ipv6.org/impl/mac.html

## IBM AIX notes

IBM AIX has a largefile problem with mkstemp.  See IBM PR-51921.
The workaround is to append the following to config.h:

>     #ifdef _LARGE_FILES
>     #undef HAVE_SECURE_MKSTEMP
>     #endif
