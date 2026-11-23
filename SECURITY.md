## Supported releases

| Line            | Status         | Upstream support                                                  | End condition                                |
| --------------- | -------------- | ----------------------------------------------------------------- | -------------------------------------------- |
| 3.5             | Current stable | Security fixes, bug fixes and compatible incremental improvements | 12 months after the first stable 4.x release |
| 3.2             | LTS            | Security fixes only                                               | 31 May 2029                                  |
| All other lines | Unsupported    | None                                                              | Support has ended                            |

Only the latest patch release in each supported line receives upstream support. The 3.2 LTS line receives confirmed security fixes plus changes required to apply or verify them. It does not receive new features, general bug fixes, performance work, modernisation or optimisation. Its current release is [3.2.8](https://github.com/RsyncProject/rsync/releases/tag/v3.2.8). An extension beyond 31 May 2029 requires an explicit policy update.

The 3.5 line receives compatible improvements which can be shipped without a major migration. Broad rewrites, removals and compatibility breaks belong in the next major release.

This policy covers upstream rsync releases. Operating-system distributions may maintain their packages under different policies and timelines.

## Responsibilities

| Responsibility                                 | Owner                                                 |
| ---------------------------------------------- | ----------------------------------------------------- |
| Security coordination, disclosure and releases | Zen Dodd ([@steadytao](https://github.com/steadytao)) |
| Triage and review                              | RsyncProject maintainers                              |

The current stable line is maintained on [stable/3.5](https://github.com/RsyncProject/rsync/tree/stable/3.5). The 3.2 LTS line is maintained on [lts/3.2](https://github.com/RsyncProject/rsync/tree/lts/3.2).

## Reporting a vulnerability

Report suspected vulnerabilities through [GitHub private vulnerability reporting](https://github.com/RsyncProject/rsync/security/advisories/new). If GitHub cannot be used, email [rsync.project@gmail.com](mailto:rsync.project@gmail.com).

Include:

- the affected version and platform;
- the relevant command, daemon configuration or restricted-shell policy;
- a minimal reproducer;
- the expected and observed behaviour;
- the security boundary and impact;
- any known workarounds or public disclosure.

Do not post undisclosed vulnerability details in public issues, pull requests, discussions or mailing lists.

## Handling and disclosure

Validation covers the reported boundary, affected versions, severity and fix. A crash, performance problem or configuration mistake is not a vulnerability without a supported path across a security boundary or a meaningful availability impact.

Fixes, release artefacts and advisories should be published together. Embargo dates are agreed with the reporter and relevant distributors after confirming the scope and release plan. A public report cannot be treated as confidential.

GitHub advisories are the public record for vulnerabilities handled by the project. Release notes summarise the fixes included in each release. The [rsync security page](https://rsync.samba.org/security.html) retains the historical advisory index.

## Security model and platform limits

Peer-supplied file lists, checksums, arguments, filters, messages and metadata are untrusted. A security boundary exists whenever rsync has more filesystem or process authority than the peer or another user who can change a path it resolves. This applies to local transfers, remote-shell transfers, daemon modules and restricted `rrsync` commands.

Where the operating system supports them, rsync uses held directory descriptors and `*at()` operations to confine security-sensitive path resolution. The remaining platform limits are:

- a platform without a usable search-only directory descriptor fails closed when safe parent traversal is unavailable;
- nested socket creation fails closed without a confined primitive, while some special-file creation retains a path-based fallback on systems without the required `*at()` operation;
- ACL, xattr and creation-time operations may retain a path-based metadata fallback where the platform has no fd-relative equivalent;
- Cygwin rejects static out-of-tree symlinks but its directory descriptors do not pin directory inodes, so local parent-swap races remain possible;
- `rrsync` can preserve a validated inode across `exec` through `/proc/self/fd` on Linux; other platforms retain realpath validation without that inode pin;
- `--insecure-links` and a daemon module's `insecure links = yes` deliberately restore legacy symlink-following behaviour.

Holding a directory descriptor does not pin every entry in that directory or provide a consistent snapshot of a tree being modified during a transfer.

Daemon authentication proves knowledge of a shared secret but does not encrypt the connection. Legacy clients can negotiate MD4 or MD5. Operators who require a strong digest should set `auth digest = sha256`; sensitive transfers should use SSH or verified TLS.

## Unsupported releases

Reports are assessed against supported releases. Unsupported lines do not receive upstream fixes or releases. Distributors may backport a fix at their own discretion. Review of a narrow backport does not restore support for that line.
