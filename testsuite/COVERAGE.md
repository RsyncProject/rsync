# rsync option / daemon-parameter test coverage matrix

Living checklist for the test-coverage effort that precedes the path-handling
restructure of rsync's path resolution. The restructure rewrites parent-directory
resolution for essentially every option, so the goal here is a regression net
that exercises each option **at directory depth** (≥3 levels) and, where the
option spans trees, **across directory boundaries**, asserting the *specific
property* the option controls — not just `dest == src`.

How to read the columns:

* **test(s)** — the `testsuite/*_test.py` that exercise the option. Tests added
  by this effort are marked `*new*`.
* **depth** — Y = asserted on entries ≥3 levels deep; `~` = exercised only at/near
  the tree root; `n/a` = not a path-resolution option.
* **x-dir** — Y = exercised with the relevant aux tree (temp/backup/dest/partial)
  **outside** the main tree; `—` = not a cross-directory option.
* **gap** — what is still missing.

Status legend: ✓ property asserted · `~` shallow / by an existing ported test ·
✗ no coverage.

---

## Command-line options

### Recursion / structure
| option | test(s) | depth | x-dir | notes / gap |
|---|---|---|---|---|
| -a, --archive | (all) | Y | — | ✓ ubiquitous |
| -r, --recursive | hands, delete-deep*new* | Y | — | ✓ |
| -R, --relative | relative, relative-implied*new* | Y | — | ✓ implied-dir attrs at depth |
| --no-implied-dirs | relative-implied*new* | Y | — | ✓ (proto 30+; proto 29 rejects multi-component path) |
| --inc-recursive / --no-inc-recursive | hardlinks | Y | — | `~` exercised, not isolated |
| -d, --dirs | dirs*new* | Y | — | ✓ no-recurse top layer |
| --old-dirs / --old-d | old-dirs | `~` | — | ✓ both mixed-version remote directions |
| -m, --prune-empty-dirs | prune-empty-dirs*new* | Y | — | ✓ incl. filter-emptied chains |

### Links
| option | test(s) | depth | x-dir | notes / gap |
|---|---|---|---|---|
| -l, --links | links*new*, symlink-ignore | Y | — | ✓ |
| -L, --copy-links | links*new* | Y | — | ✓ deref file+dir |
| -k, --copy-dirlinks | links*new* | Y | — | ✓ follow dir-symlink |
| -K, --keep-dirlinks | symlink-dirlink-basis | Y | — | ✓ #715; skips on no-RESOLVE_BENEATH / --disable-openat2 |
| -H, --hard-links | hardlinks, hardlinks-deep*new* | Y | Y | ✓ cross-directory hardlink |
| --copy-unsafe-links | unsafe-links | `~` | — | `~` |
| --safe-links | safe-links | `~` | — | `~` |
| --munge-links | (daemon-munge*new* covers the daemon param) | — | — | `~` client option not isolated; local mode is a near no-op |

### Metadata / permissions / ownership
| option | test(s) | depth | x-dir | notes / gap |
|---|---|---|---|---|
| -p, --perms | metadata-depth*new* | Y | — | ✓ exact modes per entry |
| -E, --executability | executability | `~` | — | `~` |
| --chmod | metadata-depth*new*, chmod-option | Y | — | ✓ |
| -A, --acls | acls, acls-depth*new* | Y | — | ✓ |
| -X, --xattrs | xattrs, xattrs-depth*new* | Y | — | ✓ |
| -t, --times | metadata-depth*new* | Y | — | ✓ |
| -U, --atimes | atimes | `~` | — | `~` (same set path as -t, covered deep) |
| --open-noatime | open-noatime | `~` | — | `~` |
| -N, --crtimes | crtimes | `~` | — | `~` (skips without crtimes support) |
| -O, --omit-dir-times | omit-times*new* | Y | — | ✓ |
| -J, --omit-link-times | omit-times*new* | Y | — | ✓ |
| -o, --owner | chown, ownership-depth*new* | Y | — | ✓ uid map root-gated |
| -g, --group | chgrp, ownership-depth*new* | Y | — | ✓ group remap non-root |
| --super / --fake-super | chown, chown-fake | `~` | — | `~` |
| --numeric-ids | ownership-depth | Y | — | ✓ client uid/gid mapping |
| --usermap / --groupmap | ownership-depth*new* | Y | — | ✓ groupmap non-root; usermap root-gated |
| --chown | ownership-depth*new* | Y | — | ✓ group half |
| -D / --devices / --specials | devices, devices-fake | `~` | — | `~` root/device-gated |
| --copy-devices / --write-devices | — | — | — | ✗ device-gated |
| -S, --sparse | sparse*new* | Y | — | ✓ hole preserved at depth |

### Delta / temp / backup / dest (highest restructure risk)
| option | test(s) | depth | x-dir | notes / gap |
|---|---|---|---|---|
| -T, --temp-dir | temp-dir*new*, chmod-temp-dir | Y | Y | ✓ cross-dir rename |
| --partial | partial*new* | Y | — | ✓ partial kept in dest file |
| --partial-dir | partial*new*, symlink-dirlink-basis | Y | Y | ✓ relative (in-tree) + absolute (outside), incl. delta resume from an absolute outside-tree partial |
| --delay-updates | delay-updates, delay-updates-deep*new* | Y | — | ✓ per-dir staging |
| --inplace | inplace*new*, alt-dest | Y | — | ✓ inode preserved |
| --append / --append-verify | append*new* | Y | — | ✓ verify split is proto 30+ |
| -b, --backup / --backup-dir / --suffix | backup, backup-deep*new* | Y | Y | ✓ |
| --compare-dest / --copy-dest / --link-dest | alt-dest, alt-dest-deep*new* | Y | Y | ✓ link=hardlink, copy=copy, compare=skip |
| -y, --fuzzy | fuzzy | `~` | — | `~` |
| -u, --update | update*new* | Y | — | ✓ keeps newer dest, updates older |
| -W, --whole-file | (used widely; --no-whole-file ubiquitous) | n/a | — | `~` |
| --mkpath | mkpath | `~` | — | `~` |
| -x, --one-file-system | — | — | — | ✗ (needs a mount boundary) |
| --preallocate | preallocate | Y | — | ✓ allocation and delta-update paths |
| --fsync | — | — | — | ✗ |
| -B, --block-size | hashsearch-chain, compress-zlib-insert, preallocate | Y | — | ✓ |
| --max-alloc | max-alloc-zero | — | — | ✓ zero resolves to each peer's supported ceiling; values above the limit are rejected |

### Filtering
| option | test(s) | depth | x-dir | notes / gap |
|---|---|---|---|---|
| -f, --filter / -F | filter-depth*new*, merge | Y | — | ✓ deep per-dir merge |
| --exclude / --include | filter-depth*new*, exclude, exclude-lsh | Y | — | ✓ |
| --exclude-from / --include-from | files-from-depth*new* | Y | — | ✓ |
| -C, --cvs-exclude | cvs-exclude*new* | Y | — | ✓ incl. deep .cvsignore |
| --files-from | files-from-depth*new* | Y | — | ✓ |
| -0, --from0 | files-from-depth*new* | Y | — | ✓ |
| --max-size / --min-size | size-filter*new* | Y | — | ✓ |
| --existing / --ignore-existing | delete-deep*new* | Y | — | ✓ |
| --ignore-missing-args | ignore-missing-args | Y | — | ✓ direct, remote-shell and `--files-from` sources |
| --delete-missing-args | delete-missing-args-files-from | `~` | — | ✓ with `--files-from` |

### Deletion
| option | test(s) | depth | x-dir | notes / gap |
|---|---|---|---|---|
| --delete / --del | delete, delete-deep*new* | Y | — | ✓ deep subtree |
| --delete-before/during/delay/after | delete-deep*new* | Y | — | ✓ all four agree |
| --delete-excluded | delete | `~` | — | `~` |
| --max-delete | delete-deep*new* | Y | — | ✓ caps deletions |
| --remove-source-files | delete | `~` | — | `~` |
| --force | update*new* | Y | — | ✓ replaces a non-empty dir with a file |
| --ignore-errors | — | — | — | ✗ (client; daemon `ignore errors` also ✗) |

### Comparison / checksum / compression
| option | test(s) | depth | x-dir | notes / gap |
|---|---|---|---|---|
| -c, --checksum | compare*new* | Y | — | ✓ catches stealth change |
| -I, --ignore-times | compare*new* | Y | — | ✓ |
| --size-only | compare*new* | Y | — | ✓ |
| -@, --modify-window | compare*new* | Y | — | ✓ |
| --checksum-choice / --checksum-seed | compress-options*new* | Y | — | ✓ every advertised algo |
| -z, --compress | daemon-gzip-{up,down}load, daemon-refuse-compress | `~` | — | `~` |
| --compress-choice / --compress-level / --skip-compress | compress-options*new* | Y | — | ✓ |

### Output / reporting (path-irrelevant — checked for output shape)
| option | test(s) | notes / gap |
|---|---|---|
| -i, --itemize-changes | output-options*new*, itemize | ✓ |
| -n, --dry-run | output-options*new* | ✓ |
| --stats | output-options*new* | ✓ |
| --out-format | output-options*new* | ✓ |
| --list-only | output-options*new* | ✓ |
| -q, --quiet | output-options*new* | ✓ |
| --progress / -P | output-options*new* | ✓ (--progress) |
| -h, --human-readable / -8, --8-bit-output | output-options*new* | ✓ smoke |
| --version / --help | output-options*new* | ✓ |
| --info / --debug / --stderr / --no-motd / --outbuf | output-options, daemon-module-options | ✓ output selection, routing, MOTD suppression and line buffering |
| -M, --remote-option / --log-file / --log-file-format | remote-logging | ✓ client and remote-shell sender/receiver logs |

### Batch / connection / misc
| option | test(s) | notes / gap |
|---|---|---|
| --write-batch / --only-write-batch / --read-batch | batch-mode | `~` |
| -e, --rsh / --rsync-path | ssh-basic, many | `~` |
| --protocol | check29 / check30 (whole suite) | ✓ |
| --address / --port | daemon-address-family, daemon-standalone-detach | ✓ explicit IPv4/IPv6 and config-derived binding |
| --password-file | daemon-auth*new* | ✓ |
| --early-input / daemon `early exec` | daemon-early-exec-nameconv, early-input-symlink | ✓ data delivery and confined input path |
| --sockopts | daemon-module-options | ✓ client and daemon socket options |
| --blocking-io | — | ✗ |
| --timeout / --contimeout | daemon-handshake-timeout, msg-io-timeout-{zero,overflow}, contimeout-rsh | ✓ precedence, bounds and remote-shell daemon path |
| -4/-6, --ipv4/--ipv6 | daemon-address-family | ✓ IPv4 and IPv6 daemon binding and client connection |
| --stop-after / --stop-at | stop-time | ✓ future, past and duration parsing |
| --bwlimit | partial*new* (used, not asserted) | `~` |
| --copy-as | — | ✗ root-gated |
| --iconv | iconv | ✓ filenames, arguments, file lists, protocol 30+ symlink targets and invalid input; macOS raw-byte fixture unavailable |
| -s/--secluded-args, --old-args, --trust-sender | iconv (-s); default arg protection | `~` secluded filename conversion asserted; old/trust not isolated |

---

## Daemon (rsyncd.conf) parameters

| parameter | test(s) | notes / gap |
|---|---|---|
| path | daemon-access*new*, all daemon tests | ✓ incl. deep sub-path |
| read only | daemon-access*new*, daemon | ✓ |
| write only | daemon-access*new* | ✓ |
| list | daemon-access*new*, daemon | ✓ hidden-but-usable |
| use chroot | sender-flist-symlink-leak, daemon-chroot-acl | `~` (no=most tests; yes needs root) |
| munge symlinks | daemon-munge*new* | ✓ /rsyncd-munged/ add+strip |
| exclude / include | daemon-filter*new*, daemon | ✓ exclude |
| filter / exclude from / include from | — | ✗ (exclude covers the mechanism) |
| incoming chmod | daemon-filter*new*, chmod-option | ✓ |
| outgoing chmod | daemon-filter*new* | ✓ |
| auth users / secrets file | daemon-auth*new* | ✓ accept/reject/unauth |
| strict modes | daemon-auth*new* | ✓ rejects world-readable secrets |
| refuse options | daemon-refuse*new*, daemon-refuse-compress | ✓ named/wildcard/allow-list |
| pre-xfer exec / post-xfer exec | daemon-exec*new* | ✓ env + abort |
| early exec | daemon-early-exec-nameconv | ✓ environment and `--early-input` bytes |
| hosts allow / hosts deny | daemon (allow), daemon-chroot-acl (deny) | `~` (needs --use-tcp for real peer) |
| reverse lookup / forward lookup | daemon-chroot-acl | `~` reverse only |
| log file / transfer logging / log format | daemon | `~` set, not asserted |
| max verbosity | daemon | `~` |
| comment | daemon, daemon-access*new* | ✓ |
| numeric ids | daemon-early-exec-nameconv, daemon-namecvt-* | ✓ `numeric ids = no` conversion path |
| fake super | chown-fake, daemon-namecvt-empty-response | ✓ client and daemon parameter |
| timeout | daemon-handshake-timeout | ✓ handshake and transfer precedence, including zero |
| max connections / lock file | daemon-include-maxconn, daemon-connection-limits | ✓ sequential reuse, concurrent refusal and release |
| temp dir / open noatime / ignore errors / ignore nonreadable | — | ✗ |
| charset | iconv | ✓ module charset overrides the client remote charset |
| name converter | daemon-early-exec-nameconv, daemon-namecvt-* | ✓ success, empty and malformed responses |
| dont compress | daemon-module-options | `~` configured but compression choice is not asserted |
| uid / gid / daemon uid / daemon gid / daemon chroot | build_rsyncd_conf (uid/gid when root), daemon-chroot-acl | `~` root-gated |
| motd file | daemon-module-options | ✓ banner content asserted |
| socket options | daemon-module-options | `~` live socket path; kernel effects are not introspected |
| pid file / port / address | daemon-standalone-detach, daemon-address-family | ✓ configuration and CLI binding paths |
| proxy protocol | daemon-proxy-protocol, proxy-protocol-trusted-peer | ✓ enabled, disabled and trusted-peer policy |
| listen backlog / syslog facility / syslog tag | — | ✗ |

---

## Known gaps worth a future pass
* Root-only behaviours (`-o`/`--usermap` uid remap, real devices, `use chroot
  = yes`, daemon uid/gid) skip as non-root; run the suite as root to cover.
* `--ignore-errors` and `-x/--one-file-system` have no dedicated test yet.
* `--fsync`, `--blocking-io`, daemon charset and daemon listen/syslog settings
  remain unisolated.
