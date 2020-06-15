# NEWS for rsync 3.2.0 (UNRELEASED)

Protocol: 31 (unchanged)

## Changes since 3.1.3:

### BUG FIXES:

 - Avoid a potential out-of-bounds read in daemon mode if argc can be made to
   become 0.

 - Fix the default list of skip-compress files for non-daemon transfers.

 - Fix xattr filter rules losing an 'x' attribute in a non-local transfer.

 - Avoid an error when a check for a potential fuzzy file happens to reference
   a directory.

 - Make the atomic-rsync helper script have a more consistent error-exit.

 - Make sure that a signal handler calls `_exit()` instead of exit().

 - Various zlib fixes, including security fixes for CVE-2016-9843,
   CVE-2016-9842, CVE-2016-9841, and CVE-2016-9840.

 - Fixed an issue with `--remove-source-files` not removing a source symlink
   when combined with `--copy-links`.

 - Fixed a bug where the daemon would fail to write early fatal error messages
   to the client, such as refused or unknown command-line options.

 - Fixed the block-size validation logic when dealing with older protocols.

 - Some rrsync fixes and enhancements to handle the latest options.

 - Fixed a problem with the `--link-dest`|`--copy-dest` code when `--xattrs`
   was specified along with multiple alternate-destination directories (it
   could possibly choose a bad file match while trying to find a better xattr
   match).

 - Fixed a couple bugs in the handling of files with the `--sparse` option.

 - Fixed a bug in the writing of the batch.sh file (w/--write-batch) when the
   source & destination args were not last on the command-line.

 - Avoid a hang when an overabundance of messages clogs up all the I/O buffers.

 - Fixed a mismatch in the RSYNC_PID values put into the environment of
   `pre-xfer exec` and a `post-xfer exec`.

 - Fixed a crash in the `--iconv` code.

 - Fixed a rare crash in the popt_unalias() code.

### ENHANCEMENTS:

 - Various checksum enhancements, including the optional use of openssl's MD4 &
   MD5 checksum algorithms, some x86-64 optimizations for the rolling checksum,
   some x86-64 optimizations for the (non-openssl) MD5 checksum, the addition
   of xxhash checksum support, and a negotiation heuristic that ensures that it
   is easier to add new checksum algorithms in the future.  The environment
   variable `RSYNC_CHECKSUM_LIST` can be used to customize the preference order
   of the negotiation, or use `--checksum-choice` (`--cc`) to force a choice.

 - Various compression enhancements, including the addition of zstd and lz4
   compression algorithms and a negotiation heuristic that picks the best
   compression option supported by both sides.  The environment variable
   `RSYNC_COMPRESS_LIST` can be used to customize the preference order of the
   negotiation, or use `--compress-choice` (`--zc`) to force a choice.

 - Added a --debug=NSTR option that outputs details of the new negotiation
   strings (for checksums and compression).  The first level just outputs the
   result of each negotiation on the client, level 2 outputs the values of the
   strings that were sent to and received from the server, and level 3 outputs
   all those values on the server side too (when given the debug option).

 - The --debug=OPTS command-line option is no longer auto-forwarded to the
   remote rsync which allows for the client and server to have different levels
   of debug specified. This also allows for newer debug options to be
   specified, such as using --debug=NSTR to see the negotiated hash result,
   without having the command fail if the server version is too old to handle
   that debug item. Use -M--debug=OPTS to send the options to the remote side.

 - Added the `--atimes` option based on the long-standing patch (just with some
   fixes that the patch has been needing).

 - Added `--open-noatime` option to open files using `O_NOATIME`.

 - Added the `--write-devices` option based on the long-standing patch.

 - Added openssl & preliminary gnutls support to the rsync-ssl script, which is
   now installed by default.  This was unified with the old stunnel-rsync
   helper script to simplify packaging.  Note that the script accepts the use
   of --type=gnutls for gnutls testing, but does not look for gnutls-cli on the
   path yet.  The use of type=gnutls will not work right until gnutls-cli no
   longer drops data.

 - Rsync was enhanced to set the `RSYNC_PORT` environment variable when running
   a daemon-over-rsh script. Its value is the user-specified port number (set
   via `--port` or an rsync:// URL) or 0 if the user didn't override the port.

 - Added the `proxy protocol` daemon parameter that allows your rsyncd to know
   the real remote IP when it is setup behind a proxy.

 - Added negated matching to the daemon's `refuse options` setting by using
   match strings that start with a `!` (such as `!compress*`).  This lets you
   refuse all options except for a particular approved list, for example.

 - Added the `early exec` daemon parameter that runs a script before the
   transfer parameters are known, allowing some early setup based on module
   name.

 - Added status output in response to a signal (via both SIGINFO & SIGVTALRM).

 - Added `--copy-as=USER` option to give some extra security to root-run rsync
   commands into/from untrusted directories (such as backups and restores).

 - When resuming the transfer of a file in the `--partial-dir`, rsync will now
   update that partial file in-place instead of creating yet another tmp file
   copy.  This requires both sender & receiver to be at least v3.2.0.

 - Added support for `RSYNC_SHELL` & `RSYNC_NO_XFER_EXEC` environment variables
   that affect the pre-xfer exec and post-xfer exec rsync daemon options.

 - Optimize the `--fuzzy --fuzzy` heuristic to avoid the fuzzy directory scan
   until all other basis-file options are exhausted (such as `--link-dest`).

 - Have the daemon log include the normal-exit sent/received stats when the
   transfer exited with an error when possible (i.e. if it is the sender).

 - The daemon now locks its pid file (when configured to use one) so that it
   will not fail to start when the file exists and it is unlocked.

 - Various man page improvements, including some html representations (that
   aren't installed by default).

 - Made -V the short option for --version and improved its information.

 - Forward -4 or -6 to the ssh command, making it easier to type than
   `--rsh='ssh -4'` (or -6).

### PACKAGING RELATED:

 - Add installed binary: /usr/bin/rsync-ssl

 - Add installed man page: /usr/man/man1/rsync-ssl.1

 - Tweak auxilliary doc file names, such as: README.md, INSTALL.md, NEWS.md, &
   OLDNEWS.md.

 - The rsync-ssl script wants to run openssl or stunnel4, so consider adding a
   dependency for one of those options (though it's probably fine to just let
   it complain about being unable to find the program and let the user decide
   if they want to install one or the other).

 - If you packaged rsync + rsync-ssl + rsync-ssl-daemon as separate packages,
   the rsync-ssl package is now gone (rsync-ssl should be considered to be
   mainstream now that Samba requires SSL for its rsync daemon).

 - Add _build_ dependency for liblz4-dev, libxxhash-dev, libzstd-dev, and
   libssl-dev.  These development libraries will give rsync extra compression
   algorithms, extra checksum algorithms, and allow use of openssl's crypto
   lib for (potentially) faster MD4/MD5 checksums.

 - Add _build_ dependency for g++ to enable the SIMD checksum optimizations.

 - Add _build_ dependency for _either_ python3-cmarkcfm or python3-commonmark
   to allow for patching of man pages or building a git release.  Note that
   cmarkcfm is faster than commonmark, but they generate the same data.

 - Remove yodl _build_ dependency (if it was even listed before).

### DEVELOPER RELATED:

 - Silenced some annoying warnings about major() & minor() by improving an
   autoconf include-file check.

 - Converted the man pages from yodl to markdown. They are now processed via a
   simple python3 script using the cmarkgfm **or** commonmark library.  This
   should make it easier to package rsync, since yodl has gotten obscure.

 - Improved some configure checks to work better with strict C99 compilers.

 - Some perl building/packaging scripts were recoded into awk and python3.

 - Some defines in byteorder.h were changed into static inline functions that
   will help to ensure that the args don't get evaluated multiple times on
   "careful alignment" hosts.

 - Some code typos were fixed (as pointed out by a Fossies run).

------------------------------------------------------------------------------
