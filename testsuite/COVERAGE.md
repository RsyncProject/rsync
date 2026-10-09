# Test coverage

Covered means a test directly checks the behaviour. Partial means the option is exercised without covering its whole contract. Missing identifies work still to do.

Path depth is deep, shallow, untested or N/A. Auxiliary tree is outside, inside only, untested or N/A depending on where the relevant backup, basis, partial or temporary tree is exercised.

## Recursion and structure

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-a, --archive | many | deep | N/A | Covered throughout the suite
-r, --recursive | hands, delete-deep | deep | N/A | Covered
-R, --relative | relative, relative-implied | deep | N/A | Covered with implied-directory attributes
--no-implied-dirs | relative-implied | deep | N/A | Covered for protocol 30 and later; protocol 29 rejects the multi-component case
--inc-recursive, --no-inc-recursive, --no-i-r | hardlinks | deep | N/A | Partial: exercised but not isolated
-d, --dirs | dirs | deep | N/A | Covered without recursion
--old-dirs, --old-d | old-dirs | shallow | N/A | Covered in both mixed-version remote directions
-m, --prune-empty-dirs | prune-empty-dirs | deep | N/A | Covered with filter-emptied chains

## Links

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-l, --links | links, symlink-ignore | deep | N/A | Covered
-L, --copy-links | links | deep | N/A | Covered for file and directory links
-k, --copy-dirlinks | links | deep | N/A | Covered
-K, --keep-dirlinks | symlink-dirlink-basis | deep | N/A | Covered for issue 715; unavailable without the secure path resolver
-H, --hard-links | hardlinks, hardlinks-deep | deep | outside | Covered across directories
--copy-unsafe-links | unsafe-links | shallow | N/A | Partial
--safe-links | safe-links | shallow | N/A | Partial
--insecure-links | operator-path tests, insecure-links-admin-optout | deep | N/A | Covered for the local opt-out and daemon refusal
--confine-root | files-from-leak, relative-source-ancestor, rrsync-merge-file-confine | deep | N/A | Covered for direct arguments, files-from and restricted shells
--munge-links | daemon-munge | N/A | N/A | Partial: daemon behaviour is covered but the client option is not isolated

## Metadata, permissions and ownership

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-p, --perms | metadata-depth | deep | N/A | Covered with exact per-entry modes
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
--chown | ownership-depth | deep | N/A | Partial: group handling is covered
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
--append, --append-verify | append | deep | inside only | Covered; the verification split needs protocol 30 or later
-b, --backup, --backup-dir, --suffix | backup, backup-deep | deep | outside | Covered
--compare-dest, --copy-dest, --link-dest | alt-dest, alt-dest-deep | deep | outside | Covered for skip, copy and hard-link behaviour
-y, --fuzzy | fuzzy | shallow | N/A | Partial
-u, --update | update | deep | N/A | Covered for newer and older destinations
-W, --whole-file, --no-whole-file | many | N/A | N/A | Partial: widely exercised but not isolated
--mkpath | mkpath | shallow | N/A | Partial
-x, --one-file-system | none | untested | untested | Missing; needs a mount boundary
--preallocate | preallocate | deep | N/A | Covered for allocation and delta updates
--fsync | none | untested | N/A | Missing
-B, --block-size | hashsearch-chain, compress-zlib-insert, preallocate | deep | N/A | Covered
--max-alloc | max-alloc-zero | N/A | N/A | Covered for zero and values above the peer limit

## Filtering

Option | Tests | Path depth | Auxiliary tree | Coverage
--- | --- | --- | --- | ---
-f, --filter, -F | filter-depth, merge | deep | N/A | Covered for deep per-directory merges
--exclude, --include | filter-depth, exclude, exclude-lsh | deep | N/A | Covered
--exclude-from, --include-from | files-from-depth | deep | N/A | Covered
-C, --cvs-exclude | cvs-exclude | deep | N/A | Covered with nested .cvsignore
--files-from | files-from-depth | deep | N/A | Covered
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
-c, --checksum | compare | deep | N/A | Covered for same-metadata content changes
-I, --ignore-times | compare | deep | N/A | Covered
--size-only | compare | deep | N/A | Covered
-@, --modify-window | compare | deep | N/A | Covered
--checksum-choice, --checksum-seed | compress-options | deep | N/A | Covered for advertised algorithms
-z, --compress | daemon-gzip-download, daemon-gzip-upload, daemon-refuse-compress | shallow | N/A | Partial
--compress-choice, --compress-level, --skip-compress | compress-options | deep | N/A | Covered
--compress-threads | daemon-refuse-compress-threads-alias, daemon-zstd-thread-exhaustion | N/A | N/A | Partial: refusal and the daemon worker limit are covered

## Output and reporting

Option | Tests | Coverage
--- | --- | ---
-v, --verbose | many | Partial: used widely but verbosity levels are not isolated
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
-e, --rsh, --rsync-path | ssh-basic and many others | Partial
--protocol | check29 and check30 | Covered across the selected suite
--daemon, --config, --detach, --no-detach | daemon-standalone-detach, daemon-stdin tests | Covered for detached, foreground and inherited-socket startup
--dparam | none | Missing
--address, --port | daemon-address-family, daemon-standalone-detach | Covered for IPv4, IPv6 and configured binding
--password-file | daemon-auth | Covered
--early-input | daemon-early-exec-nameconv, early-input-symlink | Covered for data delivery and confined input paths
--sockopts | daemon-module-options | Covered for client and daemon configuration
--blocking-io | none | Missing
--timeout, --contimeout | daemon-handshake-timeout, msg-io-timeout-zero, msg-io-timeout-overflow, contimeout-rsh | Covered for precedence, bounds and remote-shell daemon paths
-4, -6, --ipv4, --ipv6 | daemon-address-family | Covered for daemon binding and client connection
--stop-after, --stop-at | stop-time | Covered for future, past and duration parsing
--bwlimit | partial | Partial: used but not asserted directly
--copy-as | none | Missing and root-gated
--iconv | iconv | Covered for names, arguments, file lists, protocol 30 link targets and invalid input; the raw-byte fixture is unavailable on macOS
-s, --secluded-args, --old-args, --trust-sender | iconv, smoke | Partial: secluded and legacy argument handling are asserted; trust-sender is not isolated

## Daemon parameters

Parameter | Tests | Coverage
--- | --- | ---
path | daemon-access and daemon tests | Covered with nested paths
read only | daemon-access, daemon | Covered
write only | daemon-access | Covered
list | daemon-access, daemon | Covered for hidden but usable modules
use chroot | sender-flist-symlink-leak, daemon-chroot-acl | Partial: the enabled case needs root
insecure links | insecure-links-admin-optout, daemon-symlink-escape-matrix | Covered for the administrative opt-out
munge symlinks | daemon-munge | Covered for prefix addition and removal
exclude, include | daemon-filter, daemon | Covered for exclusion
filter, exclude from, include from | none | Missing as daemon parameters
incoming chmod | daemon-filter, chmod-option | Covered
outgoing chmod | daemon-filter | Covered
auth users, secrets file | daemon-auth | Covered for acceptance and rejection
auth digest | daemon-auth-digest-floor | Covered for minimum strength, old peers and invalid configuration
strict modes | daemon-auth | Covered for unsafe secrets-file modes
refuse options | daemon-refuse, daemon-refuse-compress | Covered for names, wildcards and allow lists
pre-xfer exec, post-xfer exec | daemon-exec | Covered for environment and abort behaviour
early exec | daemon-early-exec-nameconv | Covered for environment and early input
hosts allow, hosts deny | daemon, daemon-chroot-acl | Partial: a real TCP peer is required
reverse lookup, forward lookup | daemon-chroot-acl | Partial: reverse lookup only
log file, transfer logging, log format | daemon | Partial: configured but not asserted
max verbosity | daemon | Partial
comment | daemon, daemon-access | Covered
numeric ids | daemon-early-exec-nameconv, daemon-namecvt tests | Covered for numeric ids = no
fake super | chown-fake, daemon-namecvt-empty-response | Covered
timeout | daemon-handshake-timeout | Covered with zero and precedence
max connections, lock file | daemon-include-maxconn, daemon-connection-limits | Covered for refusal and slot reuse
temp dir, open noatime, ignore errors, ignore nonreadable | iconv | Partial: ignore errors is asserted
charset | iconv | Covered for module override of the remote charset
name converter | daemon-early-exec-nameconv, daemon-namecvt tests | Covered for success, empty and malformed responses
dont compress | daemon-module-options | Partial: configured but compression choice is not asserted
uid, gid, daemon uid, daemon gid, daemon chroot | build_rsyncd_conf, daemon-chroot-acl | Partial and root-gated
motd file | daemon-module-options | Covered for banner content
socket options | daemon-module-options | Partial: the live socket path runs but kernel effects are not inspected
pid file, port, address | daemon-standalone-detach, daemon-address-family | Covered for configuration and CLI paths
proxy protocol, proxy protocol hosts | daemon-proxy-protocol, proxy-protocol-trusted-peer | Covered for enabled, disabled and trusted-peer policy
listen backlog, syslog facility, syslog tag | none | Missing
