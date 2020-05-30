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

 - Fixed a crash in the `--iconv` code.

 - Fixed a problem with the `--link-dest`|`--copy-dest` code when `--xattrs`
   was specified along with multiple alternate-destination directories (it
   could possibly choose a bad file match while trying to find a better xattr
   match).

 - Fixed a couple bugs in the handling of files with the `--sparse` option.

 - Fixed a bug in the writing of the batch.sh file (w/--write-batch) when the
   source & destination args were not last on the command-line.

### ENHANCEMENTS:

 - Various checksum enhancements, including the optional use of openssl's MD4 &
   MD5 checksum algorithms, some x86-64 optimizations for the rolling checksum,
   some x86-64 optimizations for the (non-openssl) MD5 checksum, the addition
   of xxhash checksum support, and a negotiation heuristic that ensures that it
   is easier to add new checksum algorithms in the future.  Currently the
   x86-64 optimizations require the use of the `--enable-simd` flag to
   configure, but they will probably be enabled by default in the near future.
   The environment variable `RSYNC_CHECKSUM_LIST` can be used to customize the
   preference order of the negotiation.

 - Various compression enhancements, including the addition of zstd and lz4
   compression algorithms and a negotiation heuristic that picks the best
   compression option supported by both sides.  The environment variable
   `RSYNC_COMPRESS_LIST` can be used to customize the preference order of the
   heuristic when speaking to another rsync 3.2.0 version.

 - Added the `--atimes` option based on the long-standing patch (just with some
   fixes that the patch has been needing).

 - Added `--open-noatime` option to open files using `O_NOATIME`.

 - Added the `--write-devices` option based on the long-standing patch.

 - Added openssl support to the rsync-ssl script via its renamed helper script,
   rsync-ssl-rsh.  Both bash scripts are now installed by default (removing the
   install-ssl-client make target).  Rsync was also enhanced to set the
   `RSYNC_PORT` environment variable when running a daemon-over-rsh script. Its
   value is the user-specified port number (set via `--port` or an rsync://
   URL) or 0 if the user didn't override the port.

 - Added negated matching to the daemon's `refuse options` setting by using
   match strings that start with a `!` (such as `!compress*`).

 - Added status output in response to a signal (via both SIGINFO & SIGVTALRM).

 - Added a `--copy-as=USER` option to give some extra security to root-run
   rsync commands into/from untrusted directories (such as backups and
   restores).

 - When resuming the transfer of a file in the `--partial-dir`, rsync will now
   update that partial file in-place instead of creating yet another tmp file
   copy.  This requires both sender & receiver to be at least v3.2.0.

 - Added support for `RSYNC_SHELL` & `RSYNC_NO_XFER_EXEC` environment variables
   that affect the pre-xfer exec and post-xfer exec rsync daemon options.

 - Optimize the `--fuzzy` `--fuzzy` heuristic to avoid the fuzzy directory scan
   until all other basis-file options are exhausted (such as `--link-dest`).

 - Have a daemon that is logging include the normal-exit sent/received stats
   even when the transfer exited with an error.

 - Various manpage improvements.

### DEVELOPER RELATED:

 - Silenced some annoying warnings about major()|minor() due to the autoconf
   include-file check not being smart enough.

 - Improved some configure checks to work better with strict C99 compilers.

 - The `--debug=FOO` options are no longer auto-forwarded to the server side,
   allowing more control over what is output & the ability to request debug
   data from divergent rsync versions.

 - Some perl scripts were recoded into awk and python3.

 - Some defines in byteorder.h were changed into static inline functions that
   will help to ensure that the args don't get evaluated multiple times on
   `careful alignment` hosts.

 - Some code typos were fixed (as pointed out by a Fossies run).

------------------------------------------------------------------------------
