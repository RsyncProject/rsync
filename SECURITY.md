# Security policy

## Supported versions

Version | Support
--- | ---
Current stable line | Security and correctness fixes
3.2.x LTS | Security fixes only
Other releases | Unsupported

The `lts/3.2.7` branch is based on rsync 3.2.7 and receives fixes for
vulnerabilities which affect that line. It does not receive new features,
performance changes or general bug fixes except where a change is required to
apply or verify a security fix.

The Rsync Project maintainers own this branch. The line has no indefinite
support guarantee. Its retirement date will be announced at least six months
before support ends.

## Platform requirements

The LTS path protections require `AT_FDCWD`, `O_NOFOLLOW` and `O_DIRECTORY`.
Security-sensitive path operations fail with `ENOSYS` when the platform does
not provide these primitives. `--insecure-links` deliberately restores the
historical path handling and its symlink-race exposure.

Cygwin exposes the required interfaces but its directory descriptors are
pathname-backed rather than inode-pinned. Static symlink escapes are rejected
but the parent-replacement race protections do not establish a privilege
boundary on Cygwin.

Restricted `rrsync` requires an inode-bound `/proc/self/fd` implementation.
It refuses a restricted directory other than `/` when that facility is
unavailable. Using `/` does not establish a filesystem confinement boundary.

Some systems cannot create a socket relative to a held directory descriptor.
On those systems rsync refuses nested socket creation in hardened mode rather
than binding through an unconfined pathname. A top-level socket remains safe
because the process working directory already identifies its parent.

## Reporting a vulnerability

Report suspected vulnerabilities privately through
[GitHub's reporting form](https://github.com/RsyncProject/rsync/security/advisories/new).
If GitHub cannot be used, email
[rsync.project@gmail.com](mailto:rsync.project@gmail.com).

Include the affected version, relevant configuration, a reproducer and the
security impact. Do not post vulnerability details in public issues, pull
requests or mailing lists.
