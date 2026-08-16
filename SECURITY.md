# Security Policy

## Supported Versions

Only the current release of the software is actively supported.  If you need
help backporting fixes into an older release, feel free to ask.

## Reporting a Vulnerability

Email your vulnerability information to rsync's maintainer:

  Rsync Project <rsync.project@gmail.com>


## Approach to platform residuals

rsync hardens its security-sensitive operations — path resolution, metadata
application, file/socket creation — against local attacks such as parent-symlink
TOCTOU races. Some of these operations can only be made race-safe with a
primitive the underlying OS provides (an `*at()` syscall on a held directory fd,
an fdescfs-style `/proc/self/fd` magic symlink, `mknodat()`, the `*xattrat`
syscalls, and so on), and that primitive is not available on every supported
platform.

The guiding rule for those cases is:

> **On a modern Linux system every issue described in this document is fully
> addressed.** Where an operation *can* be secured on some platforms but *cannot*
> be secured on others, and the residual risk is a *local* privilege-escalation
> or data-disclosure class (an attacker who already has write access inside the
> transferred tree), rsync prefers keeping the operation functional on the
> platforms that lack the primitive over disabling a long-standing feature for
> everyone on those platforms.

So a hardened operation takes the race-safe path wherever the platform offers one
and falls back to the historical (path-based, unconfined) behaviour only where it
does not — rather than refusing the operation outright. Each such fallback is an
accepted residual, documented under "Known residuals" below, and on the daemon it
can be turned off per feature with `refuse options = ...`. The residuals are
therefore confined to non-Linux platforms (the BSDs, macOS, Solaris/illumos),
Cygwin, and — for a few features — pre-6.13 Linux kernels; a current, normally
configured Linux deployment carries none of them. (The `/proc/self/fd`-based
fallbacks assume a mounted `/proc`, which every standard Linux provides; a
deliberately `/proc`-less container is the one Linux case that can still hit a
residual.)

The one deliberate exception is an operation whose unconfined fallback would
*create a new filesystem object at an attacker-influenceable path* rather than set
metadata on the object rsync already transferred: the nested-socket `bind()` on
platforms without a race-safe socket-create (no `bindat()`). There the unsafe path
is an out-of-tree write/create primitive, not a same-object metadata race, and a
transferred socket inode is a worthless placeholder, so rsync refuses (skips) it
rather than keeping it functional. A leaf permission change is likewise failed
closed rather than applied through a raced symlink, but only as a rare backstop:
the common file/dir/FIFO case is secured on every platform via `fchmod` on a held
fd, so no real functionality is lost.

This trade-off applies only to these local-attacker residual classes. Remotely
reachable defects — memory safety, authentication bypass, protocol parsing, input
bounds — are fixed unconditionally on all platforms, never left as a residual.


## Robustness against malicious peers

rsync treats everything the peer sends — the file list, checksum headers,
multiplexed messages, forwarded daemon arguments, filter rules — as untrusted,
and bounds-checks it before use. A peer-triggerable crash of a connection's
worker process is treated as a defect to be fixed, even though the daemon's
fork-per-connection model confines such a fault to that one connection rather
than the whole service.

Alongside the issues enumerated elsewhere in this document, the code is hardened
continuously through protocol fuzzing (driving the daemon protocol against a
writable module) and static analysis, with a CI gate. This release closes a
batch of peer-triggerable faults found that way: NULL-dereference and
reachable-assert crashes from crafted file lists or indices, reads past a
file-list allocation (mostly bounded over-reads of an entry's extra slots),
unbounded merge-file and suffix-list recursion, and several bounded
out-of-bounds writes driven by peer-supplied lengths or option arguments. Each
is fixed at the root with a bounds or validity check plus a defence-in-depth
guard at the use site, and carries a regression test.

Two further peer-input hardenings in this release: a peer-supplied I/O-error
value (the `MSG_IO_ERROR` message and the file-list trailer) is masked to the
defined `IOERR_*` bits, so a peer cannot set arbitrary error flags in the local
`io_error` that would be stored and re-forwarded upstream; and control
characters in a (peer-controlled) filename written to the log file are escaped,
so a name carrying C0/C1 terminal-escape bytes cannot inject sequences into an
administrator's terminal when the log is viewed (CWE-117). The number of
equal-weak-checksum blocks `hash_search()` examines per offset is also bounded
(issue #217), so a crafted or degenerate checksum set with a very long
equal-checksum chain cannot drive the sender's per-offset match-verify into a
quadratic walk and pin one connection's CPU.

Contributors adding code that consumes peer input should validate it at the
point of receipt rather than relying on a downstream check.


## Symlink-race-safe path resolution

This section documents how rsync defends against parent-directory symlink races
(a TOCTOU / confused-deputy class) and the per-platform approach it takes, so
that contributors and automated agents extend the code consistently rather than
reintroducing the weakness.

### The threat

Many rsync operations resolve pathnames that an unprivileged party can partially
control: a receiver writing into a destination tree, a sender reading a source
tree, and temp and partial files, and so on. (The operator-chosen directory
paths — `--link-dest`/`--compare-dest`/`--copy-dest`/`--backup-dir`/`--temp-dir`/
`--partial-dir` — may legitimately point outside the tree, so they are resolved
by the ownership walk described under *Symlink defense for operator-supplied
paths* below rather than the strict transfer-path resolver here.) If someone who
can write inside that tree races a
parent directory component between a real directory and a symlink ("symlink
flipping"), a path-based syscall — `open`, `stat`, `chmod`, `chown`, `utimes`,
`rename`, `unlink`, `mkdir`, `mknod`, `symlink`, hard-link creation — can be
redirected to a target *outside* the intended tree. When rsync resolves that
path with more authority than the component's controller and without a
confinement boundary, this is a confused-deputy bug (e.g. a root nightly backup
capturing `/etc/shadow`, or a root receiver chmod/chown/unlink-ing a system
file).

`O_NOFOLLOW` on the final component is **not** sufficient: the *parent*
components must be resolved safely.

The boundary that matters is **authority plus confinement**, not "daemon vs
non-daemon". A non-chroot daemon module, a root-run local transfer, and a
two-user transfer are all unconfined privileged path resolvers. Where a real
confinement boundary already exists (e.g. a per-module `chroot`) that is the
strongest protection; otherwise rsync must resolve paths defensively.

A `chroot` is only a boundary for the *outer* path it confines. A daemon module
written as `path = /outer/./inner` (`use chroot = yes`) chroots to `/outer` but
treats `/inner` as the module root, so a symlink inside the module that points to
a sibling of `/inner` is still inside the chroot yet outside the module — the
inner module therefore needs the same defensive resolution as a non-chroot
module. The single gate that decides when hardened resolution applies is
"unconfined privileged resolver": `am_daemon && (!am_chrooted || module_dirlen)`
for the daemon (any non-chroot module, plus a `/./` inner-module chroot), and any
non-chroot receiver. The local sender's content open is confined the same way for
default symlink handling; only the symlink-following modes (`-L`/`--copy-links`/
`--copy-unsafe-links`/`-k`) and `--insecure-links` are excluded, so those keep
following symlinks by design.

### The mechanism

Resolution of attacker-influenceable paths goes through `secure_relative_open()`
and the `do_*_at()` wrappers in `syscall.c`, never a raw `open()`/`rename()`/
`chmod()` on a full path string. The principle is: **trust the operator-named
transfer root, and confine all resolution beneath it**, rejecting escapes via
`..` above the anchor, absolute symlinks, or out-of-tree symlinks.
`secure_relative_open()` resolves the parent directory by walking it one
component at a time on a stack of held directory fds, then operates on the final
component with an at-style call on the resulting directory fd.

For per-entry work the receiver and generator go one step further and hold the
parent directory open: `open_dir_secure()` resolves an entry's directory once
(via `secure_relative_open()`), `held_dfd_for()` caches that descriptor for the
duration of the entry, and every operation on the entry — `lstat`, the temp-file
`mkstemp`, the temp->final `rename`, `chmod`/`chown`/`utimes`, `mkdir`, special-
file and symlink creation, the delta-basis open, and the recursive delete — runs
through that one held fd via an `*at()` call (`do_*_atfd()`). Because the
descriptor is pinned to the directory inode, a parent component flipped to a
symlink *after* the open cannot redirect any of those operations. The alternate-
destination lookups are confined the same way (`basis_link_stat()` in
`generator.c` and `secure_basis_open()` in `receiver.c`), so a peer-chosen
`--link-dest`/`--compare-dest`/`--copy-dest` basis index cannot reach an
out-of-module file through a symlinked parent.

The sender's source-directory *enumeration* is confined the same way as its
content open. `send_directory()` opens each scanned directory through
`secure_opendir()` — which resolves it via `secure_relative_open()` /
`secure_relative_open_at()` and turns the held fd into the `DIR*` with
`fdopendir()` — so a parent component raced into a symlink, or (for a daemon
following mode) an in-module symlink pointing outside the module, cannot redirect
the scan to enumerate an out-of-tree directory and leak its entry names, metadata
and symlink targets. For a daemon, both the enumeration and the content open
anchor at the served module root **pinned by identity**: `module_dirfd` is opened
(`open(".")`) the moment the daemon `chdir`s into the module, while still
privileged, and module-relative paths resolve beneath that fd via
`secure_relative_open_at()`. Anchoring at the held fd rather than re-resolving the
absolute module path keeps the confinement working after the daemon drops to the
module uid even when the module sits under a directory that uid cannot traverse
(e.g. a `0700` home — re-resolving the absolute path would `EACCES`), and is
immune to the logical-path-versus-real-cwd skew a followed in-tree directory
symlink would otherwise introduce.

### Path resolution

`secure_relative_open()` resolves a path with a single portable mechanism on
every platform: a per-component walk on a stack of held directory fds. Each
component is opened relative to the held parent with `openat(parent_fd,
"component", O_NOFOLLOW)`; descending into a real subdirectory pushes its fd, a
`..` pops back to the already-held parent (a pop at the anchor is refused), and an
in-tree directory symlink is followed by reading its target and walking that off
the same stack (absolute targets refused, symlink hops bounded). The final
component is opened `O_NOFOLLOW`.

Because every component is opened relative to a *pinned* fd under `O_NOFOLLOW`,
and `..` is resolved by the held-fd stack rather than by the kernel, the walk is
race-free by construction: no rename or symlink swap of any path name can redirect
resolution outside the anchor subtree, and no kernel "beneath" primitive
(`openat2(RESOLVE_BENEATH)` / `openat(O_RESOLVE_BENEATH)`) is required. The
confinement is therefore uniform across Linux, the BSDs, macOS and
Solaris/illumos, on old and new kernels alike, with nothing to probe or fall back
to at runtime (and so no `openat2`/seccomp interaction to worry about in sandboxed
environments).  Cygwin is the exception, because its directory descriptors and
symlink emulation do not give the held-fd walk the same inode pinning — see the
Cygwin residual below.

Legitimate *in-tree* directory symlinks are followed, so `--keep-dirlinks` /
`--copy-links` and a symlinked module path keep working. A relative alternate-dest
such as `--compare-dest=../01` may legitimately climb to a sibling still inside the
module; such a `..` path is re-anchored at the module root and its in-module climb
adjudicated by the walk (the `..` pops to the held parent), while escapes above the
anchor are still rejected.

### Leaf operations

The final operation is hardened as well, following `cp`: reads use `O_NOFOLLOW`
so a flipped leaf symlink is not followed, and new or destination files are
created with `O_CREAT|O_EXCL` (rsync's temporary files use `mkstemp`) so a
planted symlink at the target cannot be written through. A leaf `chmod` is the
one operation with no portable no-follow form: it is closed by opening the leaf
`O_RDONLY|O_NOFOLLOW` and `fchmod`-ing the held fd (refusing a symlink leaf with
`ELOOP`), falling back to `fchmodat(AT_SYMLINK_NOFOLLOW)` and then the
`fchmodat2()` syscall, and failing closed with a warning rather than ever
chmod-ing through a raced leaf symlink.

### Guidance for contributors

* When adding code that performs a path-based syscall on a path that can be
  influenced by the remote peer or by another local user, use a `do_*_at()`
  wrapper (or `secure_relative_open()`), not a raw full-path syscall.
* When introducing a new operation, add a matching `do_<op>_at()` wrapper that
  resolves the parent with `secure_relative_open()` and acts via an at-style call
  on the returned dirfd.
* Do not assume a non-daemon transfer is safe; the question is whether rsync has
  more authority than whoever controls the path components.
* On platforms whose API lacks an at-style equivalent (e.g. `setattrlist()`),
  follow the residuals policy at the top of this document: for a metadata
  operation on the already-transferred object (ACLs, xattrs, crtimes, permissions)
  fall back to the path-based call to keep the feature functional and document the
  residual; but where the unsafe fallback would *create a new object on an
  unconfined path* (the nested-socket `bind()` case), refuse it instead — that is
  an out-of-tree write/create primitive, not a same-object metadata race, and the
  lost functionality is negligible.


## Symlink defense for operator-supplied paths

rsync opens several operator-supplied paths during normal operation.  These fall
into two groups, both governed by the same ownership-walk policy below:

* operator **files**: `--log-file`, `--password-file`, `--early-input` (a client
  read whose contents are forwarded to the daemon's early-exec), `--files-from`,
  `--include-from`, `--exclude-from`, `--filter=. file`, `--write-batch`,
  `--read-batch`, per-directory filter merge files (`-C` / `-F` / `dir-merge`),
  and on the daemon side `motd file =`, `secrets file =`, `lock file =`, and
  `rsyncd.conf` itself.
* operator **directories**: `--backup-dir`, `--temp-dir`/`-T`, `--partial-dir`,
  and the `--link-dest`/`--compare-dest`/`--copy-dest` basis lookup.  These take
  a directory the operator chose, which may legitimately point outside the
  transfer tree (`--backup-dir=/var/backups`), so they are resolved with the
  ownership walk rather than the strict transfer-path resolver.

The daemon module-root `chdir()` under
`use chroot = no` and the non-daemon receiver's `chdir()` into the
operator-named destination directory are in the same class: both follow
the operator's/root's own symlinked target (the `/backup -> /mnt/disk`
admin pattern) but refuse one an attacker raced in from another uid,
unless `--insecure-links` restores the legacy plain `chdir()`.

Each of these reads or writes a path the operator or sender chose, which
may transit attacker-influenceable parent directories (the `/tmp/somedir/`
class) or be planted directly (the `/home/$user/.cvsignore` class when
root runs `rsync -a /home /backup`).

rsync's defense, applied uniformly to all of the above, is a
component-by-component path walk (`open_no_attacker_symlinks` in
`util1.c`) that allows symlinks **only** when the symlink itself is owned
by uid 0 or the running process's effective uid.  Symlinks owned by any
other uid are refused with `ELOOP` at any path component (parent or leaf).
Plain `O_NOFOLLOW` would be leaf-only and would not defend the
`/tmp/somedir/log` parent-component plant; this walk does.

The trust model preserves legitimate setups such as `/var/log -> /data/log`
(root-owned dir-symlink) and a non-root user's own `~/log -> /data/me`
symlink; it refuses an attacker's `/tmp/somedir -> /attack/path` plant.
For `--read-batch` an additional `fstat()` check refuses non-regular
files (FIFOs, devices) at the batch path, since the batch content drives
the receiver's protocol parser.

**Policy.** A symlink at **any** path component (parent or leaf) is **followed
iff it is owned by uid 0 or the process's effective uid, and refused (`ELOOP`)
otherwise**, identically for **absolute and relative** operator paths.  The trust
signal is **authority (ownership)**, not **location**: an operator path may
legitimately point outside the transfer tree, so it cannot be confined by
location the way a transfer path is.  This is deliberately distinct from the
transfer-path resolver `secure_relative_open()` (see *Symlink-race-safe path
resolution* above), which refuses **all** symlinks and anchors **beneath the
transfer root** — correct for peer-named paths, which never legitimately escape.
For the operator directory paths, a refused symlink simply makes the target look
absent (no backup/temp/basis is taken through it) and the transfer proceeds
normally; the operator's own symlinked target keeps working.

**The daemon `exclude`/`filter` chain is not a symlink boundary.** The daemon
filter chain (`exclude`, `exclude from`, `filter`, …) matches the *logical*
module-relative **name** of each item, not the physical file it resolves to.  It
is a visibility/tamper filter — a peer cannot *name* a daemon-excluded path to
pull, push to, or delete it — but it is **not** a security boundary against
symlinks: an in-module symlink whose own name is not excluded can be followed to
an excluded target (the name the filter sees, e.g. `link`, is not the excluded
name, e.g. `secret`).  This is by design and is the long-standing behaviour of
stock rsync; the defense for a writable module against symlink trickery is
`munge symlinks` (enabled by default for a writable, non-chrooted module), **not**
the filter.  Do not rely on `exclude`/`filter` to confine a peer who can introduce
or traverse a symlink; see `rsyncd.conf(5)` ("filter" and "munge symlinks").

What *is* enforced for a *peer-supplied* operator path (`--partial-dir`,
`--backup-dir`, the alt-dest basis) is confinement to the **module root**: the
ownership walk refuses a foreign-uid symlink (the symlink-race defense) and
refuses a resolved target *outside* the module.  That module-boundary confinement
is independent of `exclude`/`filter` — it holds whether or not the module sets an
exclude — and is what the operator-path tests cover.

**`--insecure-links`.** This flag is a **local** opt-out that restores the legacy
follow-any-symlink behaviour for the paths above.  It is **not forwarded** to the
remote (a remote-shell peer that wants the opt-out must set it on its own side,
e.g. via `--rsync-path`), and a **daemon never honors it**: the opt-out predicate
reads the client-controllable flag only off a daemon, so a peer-forwarded or
`-M`-injected `--insecure-links` cannot weaken a daemon's confinement — the daemon
additionally hard-refuses it (drops the connection) via the refused-options path.
A daemon admin who wants the legacy behaviour for one isolated/trusted module
sets `insecure links = yes` in that module's `rsyncd.conf` stanza (see
`rsyncd.conf(5)`); this is admin-only and re-opens the symlink-escape
vulnerabilities for that module on purpose.  The `operator-path-*` and
`insecure-links-*` tests enforce this consistency across every path-taking
option and across absolute/relative, leaf/parent, and same-uid/cross-uid plants.

For `support/rrsync` (the SSH-restricted-rsync wrapper), the same TOCTOU
class is closed in Python by opening each validated path component with
`O_RDONLY|O_NOFOLLOW`, verifying via `readlink('/proc/self/fd/N')` that the
pinned inode is still in-tree, and passing `/proc/self/fd/N` as the exec'd
rsync's argument (so the kernel routes the child's open through the pinned
inode rather than re-resolving the path).  A receiver-side new destination
has no inode of its own yet, so its existing parent directory is pinned the
same way and the leaf is created at `/proc/self/fd/<parent>/<leaf>`.  This pin
relies on an fdescfs-style magic symlink and is not available on every
platform -- see the rrsync residual below.

### Known residuals

The following are documented as out of scope for this release:

* The source-directory *enumeration* confinement needs `fdopendir()` (to form a
  `DIR*` from the securely-resolved held fd) and `dirfd()`; on a platform lacking
  either, `send_directory()` falls back to the legacy `opendir()` on the path, so
  the scan is unconfined there — the same resolver-fallback shape as the other
  `*at()`-less residuals.  Every current target provides both; the per-entry
  operations and the content open remain confined regardless.

* On **Cygwin**, the per-component held-fd walk does not provide the same
  inode-pinning guarantee as on a POSIX kernel: Cygwin tracks a process's
  current directory and resolves directory descriptors by path name rather than
  by a pinned inode, and emulates symlinks as special files.  Static out-of-tree
  symlinks are still refused (the walk sees and rejects them), and a daemon
  module path anchored at an absolute `module_dir` is confined; but an entry
  whose parent component is *raced* from a directory to a symlink mid-resolution
  can still slip past confinement that is anchored at the process CWD (e.g. the
  sender's content open), because the descriptor is not bound to the original
  inode.  The parent-component symlink-race tests are therefore not enforced on
  Cygwin (see `RSYNC_EXPECT_SKIPPED` in `.github/workflows/cygwin-build.yml` and
  the Cygwin-only xfail in `symlink-race-source_test.py`).  Cygwin is a
  development/interoperability target, not a privilege boundary host, so this is
  accepted for this release.

* On a platform with no `mknodat()` at all -- macOS before 13 is the
  supported example, where `mknod()` and `mkfifo()` exist but neither
  `mknodat()` nor `mkfifoat()` does -- creating a device node or FIFO
  falls back to plain `do_mknod()`, which resolves the whole path by name.
  What is lost is the *pinned parent*: the directory components are
  re-resolved by the kernel at create time, so an attacker who can swap a
  parent component races the create and can place the node outside the
  transfer.  The final component is not at risk -- `mknod()` and
  `mkfifo()` do not follow a symlink at the leaf, they fail `EEXIST`.
  Where `AT_FDCWD` exists -- which is every platform rsync 3.5.0 supports,
  macOS 10.13 included -- fake-super placeholders still return through
  `openat(..., O_NOFOLLOW)`, reached before either `*at` primitive is
  tested, so ordinary in-tree placeholder creation stays confined;
  fake-super loses parent confinement and the `O_NOFOLLOW` leaf only on the
  paths that reach plain `do_mknod()` (the cache-declined/cross-tree
  wrapper and the backup paths).  On a build with no `AT_FDCWD` at all
  there is no fd-relative primitive of any kind, so nothing above applies
  and every special-file create, fake-super included, is unconfined.  Transferring specials there (`--devices`, `--specials`) carries
  the parent-component race.  `symlink-mknod-fakesuper-symlink-race` skips
  itself on such a build, since the property it asserts is one the build
  deliberately does not have.

* On platforms where `mknod()`/`mknodat()` cannot create a socket inode
  (the BSDs, macOS, Solaris), a transferred socket is recreated with
  `socket()` + `unlink` + `bind(path)`, which cannot be confined (there is
  no portable `bindat()`).  Linux creates it race-safely with `mknodat()`
  on a held dirfd; on the others a *nested* socket is skipped with a
  warning rather than bound on an unconfined path, leaving only a
  top-level, operator-named socket binding by path.

* `support/rrsync`'s race-free inode-pin -- of both existing path
  components and a new destination's parent -- depends on materialising a
  held fd as a path that the exec'd rsync re-resolves to the same inode.
  rrsync validates and pins in its own process, but it then *exec*s a
  separate rsync that re-resolves the paths from `argv`, so the confining
  reference must be expressible as an argument.  A held dirfd is not: it is
  usable as a path only through an fdescfs-style magic symlink.  rrsync
  implements this for Linux only, via `/proc/self/fd/N`; it does not use the
  `/dev/fd/N` equivalent that macOS/FreeBSD expose with `fdescfs` mounted.  So on
  every non-Linux platform (the BSDs, macOS, Solaris -- whose `/proc/self/fd`
  entries are not magic symlinks -- and Cygwin), and on a `/proc`-less Linux
  namespace, rrsync falls through to the realpath-validated path unpinned,
  so a parent-component or between-pin-and-exec flip remains possible there;
  a deeper `-R` new path whose parent does not exist yet is likewise
  unpinned.  The portable closure is an rsync-side fd-passing API -- rrsync
  hands rsync the confined dirfd (inherited across `exec`) and rsync
  resolves that argument relative to it with the same `secure_relative_open`
  resolver the daemon uses, needing no magic-symlink filesystem -- a
  protocol/CLI addition under discussion on the rsync-security list.

* The operator-directory ownership walk refuses a foreign-owned symlink on a
  `--backup-dir`/`--temp-dir`/`--partial-dir`/`--link/compare/copy-dest` path, so
  a *statically planted* symlink is rejected and the dependent operation does not
  escape.  Both the data writes and the *source-metadata reads* of those
  operations are now confined to held no-follow fds: the `--copy-dest`
  `copy_file()`/`copy_xattrs()` source read goes through the held basis content fd
  (`sys_fgetxattr`), and `make_backup()` reads the backed-up file's ACL/xattrs
  through a `backup_source_fd()`-pinned fd -- so a parent-component flip can no
  longer redirect them to disclose an out-of-module value.  The cross-tree
  metadata *apply* on those leaves (the `%stat`/ACL/xattr write on a
  `--temp-dir`/`--backup-dir` staging file) is fd-pinned the same way, now
  including under `--fake-super`: the `set_file_attrs()` no-follow leaf fd was
  previously opened only when `am_root >= 0`, so a `fake super = yes` daemon fell
  back to a path-based `sys_lsetxattr()`/chmod a raced parent could redirect; the
  pin is now opened for fake-super too (a raced leaf is refused, not redirected).
  Two narrow follow-ons
  re-resolve the (now-validated) operator path by name and remain a
  *post-validation* parent-component race:
    * the in-place backup (`--inplace --backup`) writes the backup file's data
      through a confined create, but its `set_file_attrs()` metadata set
      (chmod/chown/times) re-resolves the `--backup-dir` path by name afterwards
      (it is not placed under operator mode, which would force the shared
      `set_file_attrs()` path off its held-O_NOFOLLOW-fd xattr write and re-open
      the very parent-symlink xattr race `copy-xattrs-symlink-race` pins closed); and
    * the abbreviated-xattr optimisation reuses a basis xattr value for the
      destination only when its checksum matches the digest the sender sent; that
      basis read (`rsync_xal_set()`) re-resolves the basis path by name.  This is a
      *constrained checksum-oracle*, not a disclosure: it confirms that some raced
      out-of-module xattr hashes to a value the sender already chose, rather than
      copying an unknown value onto a readable file, and needs a colluding sender
      plus a local racer.
  An attacker who flips a parent component in the window *after* the confined data
  write/stat can thus still affect those narrow metadata/oracle operations.  This
  is the same local-attacker post-confinement TOCTOU class as the ACL/crtimes
  residuals below; the data-write and direct source-read escapes are closed, and
  `--insecure-links` (or a module's `insecure links = yes`) is orthogonal to it.

* POSIX ACL application (`-A`/`--acls`) is race-safe on every Linux kernel —
  6.13+ via the `*xattrat` syscalls (or a patched libacl's `*_at` bindings), and
  older kernels via the `/proc/self/fd` compat that pins the same inode, provided
  `procfs` is mounted — and a transferred file/dir/FIFO has its xattrs (`-X`)
  applied through the held no-follow fd, so the apply cannot be redirected by a
  raced parent component.  Where neither primitive is available — the BSDs,
  Solaris and macOS (no `*xattrat` syscalls and no `/proc/self/fd` magic
  symlinks), plus the edge case of a Linux instance with no usable `/proc` (a
  `/proc`-less container/namespace) — the ACL apply falls back to the path-based
  `acl_set_file()` /
  `sys_acl_*file()` calls — the long-standing 3.4.x behaviour — to keep `--acls`
  functional rather than silently skipping it, so a parent-component flip can
  have the received ACL written onto an object outside the module/destination
  boundary (and, because the attacker controls the ACL bytes, granted to a chosen
  uid).  As with the macOS crtime tier below, this is an accepted residual under
  the functionality-over-refusal policy; a daemon operator who does not want it
  can disable the feature with `refuse options = acls`.

* macOS creation-time (`--crtimes`) preservation uses the path-based
  `setattrlist()`/`getattrlist()` with `FSOPT_NOFOLLOW`, which protects only
  the final component; there is no `setattrlistat()` targeting
  `ATTR_CMN_CRTIME`.  As with POSIX ACLs where the OS offers no race-safe
  primitive, `--crtimes` is kept functional (daemon and non-daemon) and the
  parent-component symlink race is an accepted residual: an attacker who
  flips a parent component can have a crtime read/write target an object
  outside the module/destination boundary.  The mtime/atime path is *not*
  affected -- `set_times()` resolves it race-safely through `utimensat()` on a
  held dirfd in hardened mode.  A daemon operator who does not want the crtime
  residual can disable the feature with `refuse options = crtimes` in
  `rsyncd.conf`.

* Pulling with `-o`/`-g` (or `-a`) **as root from an untrusted sender** is by
  design a trust relationship, not a confinement boundary: the sender dictates
  each received file's owner/group, including uid/gid 0.  rsync maps the
  sender's id/name pairs through the local id database; an empty or unknown
  sender name falls back to the sender's numeric id (the value `--numeric-ids`
  would use), and a sender can equally request root via the literal name
  `root`.  A root receiver must therefore only pull with `-o`/`-g` from a
  trusted source (or use a non-root receiver / a uid-gid policy).  The daemon
  *name-converter* path is guarded separately — an unknown name there maps to
  the sender's numeric id rather than 0 (see `clientserver.c`).

## Daemon authentication digest

Daemon authentication is a secret-prefix challenge-response: the client returns
`base64(H(secret || challenge))`, where `H` is a digest the two sides negotiate.
The negotiation is unauthenticated and ordered by the connecting side, and the
`md5`/`md4` digests remain available for backward compatibility, so a peer that
sends no digest list (any rsync before 3.2.0, including the openrsync that ships
with macOS) falls back to `md5` (or `md4` below protocol 30), and an on-path
attacker can rewrite the negotiation to force `md5`/`md4` even between two modern
peers.  This is **not** an authentication bypass — `md4`/`md5` have no practical
preimage break — but a weak digest makes a *captured* `(challenge, response)`
pair far cheaper to brute-force offline, recovering a guessable shared secret.

The challenge itself is seeded from the kernel CSPRNG (`/dev/urandom`), so it is
an unpredictable per-connection nonce. An earlier time/pid-based challenge was
low-entropy enough (~35 bits) that recovering the `(sec, usec, pid)` tuple from
one observed challenge let an on-path observer predict every subsequent challenge
from that daemon process and pre-compute a dictionary against a captured
response. (If `/dev/urandom` is unavailable the daemon logs a warning and falls
back to the legacy time-based challenge rather than a constant.)

A daemon operator whose clients are all modern (rsync 3.2.7+ built with openssl,
when the SHA digests were added) can require a strong digest with the `auth
digest` module parameter, e.g. `auth digest = sha256`, which refuses any
connection that negotiates — or falls back to — a weaker digest (see
`rsyncd.conf`).

Residual: there is **no default floor**, because requiring one would break every
pre-3.2.0 client (notably the macOS-bundled openrsync, which authenticates only
with `md4`).  An operator who cannot raise the floor should run the daemon behind
a verified TLS transport (`rsync-ssl`/stunnel) or over ssh — which removes the
on-path capture/downgrade vector at the transport layer — and should use a
high-entropy shared secret, which is infeasible to brute-force regardless of the
digest.
