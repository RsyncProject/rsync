# Options and daemon parameters

Covered means a test directly checks the stated behaviour. Partial means the assertions leave part of that behaviour unchecked. Missing identifies an option or parameter without a focused test. Test names refer to scripts under testsuite/tests; forced-protocol suite runs are listed by their Make targets.

Path depth is deep, shallow, untested or N/A. Auxiliary tree is outside, inside only, untested or N/A depending on where the relevant backup, basis, partial or temporary tree is exercised.

## Recursion and structure

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-a, --archive | local-transfers, metadata-depth | deep | N/A | Covered for file data and metadata
-r, --recursive | local-transfers, delete-deep | deep | N/A | Covered
-R, --relative | relative-paths, relative-implied | deep | N/A | Covered with implied-directory attributes
--no-implied-dirs | relative-implied | deep | N/A | Covered for protocol 30 and later; protocol 29 rejects the multi-component case
--inc-recursive, --no-inc-recursive, --no-i-r | hard-links | deep | N/A | Partial: exercised but not isolated
-d, --dirs | nonrecursive-directories | deep | N/A | Covered without recursion
--old-dirs, --old-d | old-dirs | shallow | N/A | Covered in both mixed-version remote directions
-m, --prune-empty-dirs | prune-empty-dirs | deep | N/A | Covered with filter-emptied chains

## Links

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-l, --links | links, symlink-ignore | deep | N/A | Covered
-L, --copy-links | links, copy-links-open-error | deep | N/A | Covered for file and directory links and preserved file-open errors
-k, --copy-dirlinks | links | deep | N/A | Covered
-K, --keep-dirlinks | symlink-dirlink-basis | deep | N/A | Covered for issue 715; unavailable without the secure path resolver
-H, --hard-links | hard-links, hard-links-depth, hard-links-emlink | deep | outside | Covered across directories and hard-link creation failure
--delay-symlinks | delay-symlinks, delay-symlinks-remote, rrsync-no-overwrite-delay-symlinks | shallow | N/A | Covered for creation order, remote propagation and restricted-shell refusal
--copy-unsafe-links | safe-links | shallow | N/A | Partial
--safe-links | safe-links | shallow | N/A | Partial
--insecure-links | operator-path-file-options, operator-path-insecure-links-refused, insecure-links-admin-optout | deep | N/A | Covered for the local opt-out and daemon refusal
--confine-root | filter-file-confinement, relative-source-ancestor, rrsync-merge-file-confine | deep | N/A | Covered for direct arguments, files-from and restricted shells
--munge-links | daemon-munge | N/A | N/A | Partial: daemon behaviour is covered but the client option is not isolated

## Metadata, permissions and ownership

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-p, --perms | metadata-depth, symlink-receiver-permissions | deep | N/A | Covered with exact per-entry modes and unsupported symlink chmod
-E, --executability | executability | shallow | N/A | Partial
--chmod | metadata-depth, chmod-option | deep | N/A | Covered
-A, --acls | acls, acls-depth | deep | N/A | Covered when ACL support is available
-X, --xattrs | xattrs, xattrs-depth | deep | N/A | Covered when extended attributes are available
-t, --times | metadata-depth | deep | N/A | Covered
-U, --atimes | atimes | shallow | N/A | Partial
--open-noatime | open-noatime | shallow | N/A | Partial
-N, --crtimes | crtimes | shallow | N/A | Partial and platform-dependent
-O, --omit-dir-times | omit-times | deep | N/A | Covered
-J, --omit-link-times | omit-times | deep | N/A | Covered
-o, --owner | chown, ownership-depth | deep | N/A | Covered with root-gated UID mapping
-g, --group | chgrp, ownership-depth | deep | N/A | Covered with non-root group remapping
--super, --fake-super | chown, chown-fake | shallow | N/A | Partial
--numeric-ids | ownership-depth | deep | N/A | Covered for client UID and GID mapping
--usermap, --groupmap | ownership-depth | deep | N/A | Covered; user mapping needs root
--chown | ownership-depth | deep | N/A | Covered for group remapping and root-only owner remapping
-D, --devices, --specials | devices, devices-fake | shallow | N/A | Partial and privilege-dependent
--drop-D | rrsync-specials-denied | N/A | N/A | Covered for restricted receivers
--copy-devices, --write-devices | none | untested | N/A | Missing
-S, --sparse | sparse | deep | N/A | Covered with a hole at depth

## Delta, temporary, backup and basis paths

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-T, --temp-dir | temp-dir, chmod-temp-dir | deep | outside | Covered across filesystems
--partial | partial | deep | inside only | Covered with a retained destination partial
--partial-dir | partial, symlink-dirlink-basis | deep | outside | Covered for relative and absolute partial directories with delta resume
--delay-updates | delay-updates, delay-updates-deep | deep | inside only | Covered for per-directory staging
--inplace | inplace, alt-dest | deep | inside only | Covered with inode preservation
--append, --append-verify | append, append-abort-mid-write | deep | inside only | Covered for interrupted writes; the verification split needs protocol 30 or later
-b, --backup, --backup-dir, --suffix | backup, backup-deep | deep | outside | Covered
--compare-dest, --copy-dest, --link-dest | alt-dest, alt-dest-deep, alt-dest-module-escape, operator-path-alt-dest | deep | outside | Covered for skip, copy, hard-link and confinement behaviour
-y, --fuzzy | fuzzy | shallow | N/A | Partial
-u, --update | update | deep | N/A | Covered for newer and older destinations
-W, --whole-file, --no-whole-file | local-transfers, checksum-chain-limit | N/A | N/A | Partial: forced delta transfer is covered; explicit whole-file mode is not
--mkpath | mkpath | shallow | N/A | Partial
-x, --one-file-system | none | untested | untested | Missing; needs a mount boundary
--preallocate | preallocate | deep | N/A | Covered for allocation and delta updates
--fsync | fsync-linux | shallow | N/A | Covered on Linux for successful and failed flushes
-B, --block-size | checksum-chain-limit, compress-zlib-insert, preallocate | deep | N/A | Covered
--max-alloc | max-alloc-zero | N/A | N/A | Covered for zero and values above the peer limit

## Filtering

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-f, --filter, -F | filter-depth, multiple-sources | deep | N/A | Covered for deep per-directory merges
--exclude, --include | filter-depth, exclude, exclude-remote-shell | deep | N/A | Covered
--exclude-from, --include-from | files-from-depth | deep | N/A | Covered
-C, --cvs-exclude | cvs-exclude | deep | N/A | Covered with nested .cvsignore
--files-from | files-from-depth, daemon-files-from-subdir | deep | N/A | Covered for nested paths and daemon module subdirectories
-0, --from0 | files-from-depth | deep | N/A | Covered
--max-size, --min-size | size-filter | deep | N/A | Covered
--existing, --ignore-non-existing, --ignore-existing | delete-deep | deep | N/A | Covered
--ignore-missing-args | ignore-missing-args | deep | N/A | Covered for direct, remote-shell and files-from inputs
--delete-missing-args | delete-missing-args-files-from | shallow | N/A | Covered with files-from

## Deletion

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
--delete, --del | delete, delete-deep | deep | N/A | Covered for deep subtrees
--delete-before, --delete-during, --delete-delay, --delete-after | delete-deep | deep | N/A | Covered
--delete-excluded | delete | shallow | N/A | Partial
--max-delete | delete-deep | deep | N/A | Covered
--remove-source-files | delete | shallow | N/A | Partial
--force | update | deep | N/A | Covered when replacing a non-empty directory with a file
--ignore-errors | iconv | N/A | N/A | Covered for deletion after sender input failure

## Comparison, checksum and compression

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-c, --checksum | comparison-options | deep | N/A | Covered for same-metadata content changes
-I, --ignore-times | comparison-options | deep | N/A | Covered
--size-only | comparison-options | deep | N/A | Covered
-@, --modify-window | comparison-options | deep | N/A | Covered
--checksum-choice, --checksum-seed | compress-options | deep | N/A | Covered for advertised algorithms
-z, --compress | daemon-gzip, daemon-refuse-compress | shallow | N/A | Partial
--compress-choice, --compress-level, --skip-compress | compress-options | deep | N/A | Covered
--compress-threads | daemon-refuse-compress-threads-alias, daemon-zstd-thread-exhaustion | N/A | N/A | Partial: refusal and the daemon worker limit are covered

## Output and reporting

Option | Tests | Coverage
--- | --- | ---
-v, --verbose | local-transfers, hard-links | Partial: verbosity levels are not compared
-i, --itemize-changes | output-options, itemize | Covered
-n, --dry-run | output-options | Covered
--stats | output-options | Covered
--out-format | output-options | Covered
--list-only | output-options | Covered
-q, --quiet | output-options | Covered
--progress, -P | output-options | Covered for progress output
-h, --human-readable, -8, --8-bit-output | output-options | Covered with format assertions
--version, --help | output-options | Covered
--info, --debug, --stderr, --no-motd, --outbuf | output-options, daemon-module-options | Covered for selection, routing, MOTD suppression and line buffering
-M, --remote-option, --log-file, --log-file-format | remote-logging | Covered for client and remote-shell sender or receiver logs

## Batch, connection and miscellaneous options

Option | Tests | Coverage
--- | --- | ---
--write-batch, --only-write-batch, --read-batch | batch-mode | Partial
-e, --rsh, --rsync-path | remote-shell-transfers | Covered for remote-shell transfer and deletion
--protocol | make check29, make check30 | Covered by forced-protocol suite runs
--daemon, --config, --detach, --no-detach | daemon-standalone-detach, daemon-stdin-inetd, daemon-stdin-local-socket, daemon-stdin-unix-listener, daemon-config-truncation, stdio-read-errors | Covered for startup, truncated configuration and configuration read errors
--dparam | none | Missing
--address, --port | daemon-address-family, daemon-standalone-detach | Covered for IPv4, IPv6 and configured binding
--password-file | daemon-auth, password-file-first-line | Covered for authentication and first-line parsing
--nice, --no-nice | niceness | Covered for local, remote-shell, daemon and restricted-shell priorities
--early-input | daemon-early-exec, early-input-symlink | Covered for data delivery and confined input paths
--sockopts | daemon-module-options | Covered for client and daemon configuration
--blocking-io | none | Missing
--timeout, --contimeout | daemon-handshake-timeout, io-timeout-zero, io-timeout-bounds, remote-shell-connect-timeout | Covered for precedence, bounds and remote-shell daemon paths
-4, -6, --ipv4, --ipv6 | daemon-address-family | Covered for daemon binding and client connection
--stop-after, --stop-at | stop-time | Covered for future, past and duration parsing
--bwlimit | partial | Partial: used but not asserted directly
--copy-as | none | Missing and root-gated
--iconv | iconv | Covered for names, arguments, file lists, protocol 30 link targets and invalid input; the raw-byte fixture is unavailable on macOS
-s, --secluded-args, --old-args, --trust-sender | iconv, secluded-args-transfer, cli-transfers | Partial: secluded and legacy argument handling are asserted; trust-sender is not isolated

## Daemon parameters

Parameter | Tests | Coverage
--- | --- | ---
path | daemon-access, daemon-listings | Covered with nested paths
read only | daemon-access, daemon-listings | Covered
write only | daemon-access | Covered
list | daemon-access, daemon-listings | Covered for hidden but usable modules
use chroot | sender-flist-symlink-leak, daemon-chroot-acl, daemon-chroot-cwd | Covered for root-directory entry, inner-module selection and failed directory changes; requires root
insecure links | insecure-links-admin-optout, daemon-symlink-escape-matrix | Covered for the administrative opt-out
munge symlinks | daemon-munge | Covered for prefix addition and removal
exclude, include | daemon-filter, daemon-listings | Covered for exclusion
filter, exclude from, include from | none | Missing as daemon parameters
incoming chmod | daemon-filter, chmod-option | Covered
outgoing chmod | daemon-filter | Covered
auth users, secrets file | daemon-auth | Covered for acceptance and rejection
auth digest | daemon-auth-digest-floor | Covered for minimum strength, old peers and invalid configuration
strict modes | daemon-auth | Covered for unsafe secrets-file modes
refuse options | daemon-refuse, daemon-refuse-compress | Covered for names, wildcards and allow lists
pre-xfer exec, post-xfer exec | daemon-exec | Covered for environment and abort behaviour
early exec | daemon-early-exec, stdio-read-errors | Covered for environment, early input and interrupted or failed input reads
hosts allow, hosts deny | daemon-access-ip, daemon-access-hostname, daemon-chroot-acl | Covered for address, CIDR and hostname rules over TCP
reverse lookup, forward lookup | daemon-chroot-acl, daemon-access-hostname | Covered for reverse lookup in a chroot and forward lookup enabled or disabled
log file, transfer logging, log format | daemon-listings | Partial: configured but not asserted
max verbosity | daemon-listings | Partial
comment | daemon-listings, daemon-access | Covered
numeric ids | daemon-name-converter, daemon-name-converter-empty-response | Partial: numeric ids = no is exercised with name conversion
fake super | chown-fake, daemon-name-converter-empty-response | Covered
timeout | daemon-handshake-timeout | Covered with zero and precedence
max connections, lock file | daemon-connection-limits | Covered for refusal and slot reuse
temp dir, open noatime, ignore errors, ignore nonreadable | iconv | Partial: ignore errors is asserted
charset | iconv | Covered for module override of the remote charset
name converter | daemon-name-converter, daemon-name-converter-empty-response, daemon-name-converter-newline | Covered for mapping, empty responses and newline-bearing requests
dont compress | daemon-module-options | Partial: configured but compression choice is not asserted
uid, gid, daemon uid, daemon gid, daemon chroot | daemon-listings, daemon-chroot-acl | Partial: transfer and access checks run but effective worker IDs are not asserted
motd file | daemon-module-options, stdio-read-errors | Covered for banner content and interrupted or failed reads
socket options | daemon-module-options | Partial: the live socket path runs but kernel effects are not inspected
pid file, port, address | daemon-standalone-detach, daemon-address-family | Covered for configuration and CLI paths
proxy protocol, proxy protocol hosts | daemon-proxy-protocol, proxy-protocol-trusted-peer | Covered for enabled, disabled and trusted-peer policy
listen backlog, syslog facility, syslog tag | none | Missing

## Interoperability

The interoperability test checks bytes, modes, integer timestamps, relative and dangling symlink targets, hard-link groups, deletion and checksum updates with unchanged file size and mtime. Ubuntu also exercises the fixture over restricted SSH with pinned host keys and command and path refusal checks.

The CI Interoperability group defines 168 cases across seven native endpoints, including all 30 directed non-Linux pairs. Case completion, destination verification and network cleanup feed the Required gate. Seven-platform execution, fork-PR authentication, concurrent-run isolation and cancellation recovery remain subject to live staging qualification.
