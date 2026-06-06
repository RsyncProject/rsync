# Old rsync version archive

Static rsync binaries built from historical release tags. Two uses:

1. **Cross-version behaviour checks** — confirming whether a behaviour a user
   reported on an old release is version-specific or option-driven.
2. **The version-mixing test suite** — `runtests.py --rsync-bin2=...` runs the
   current code against one of these as the daemon / remote-shell peer; CI
   (`.github/workflows/ubuntu-version-mix.yml`) does this for every binary
   here against the per-version manifests in `testsuite/expect/`.

Binaries are **statically linked** so they run regardless of the host's
shared libraries, and named `rsync_<version>`:

| Binary         | Version | Protocol | Notes                                   |
|----------------|---------|----------|-----------------------------------------|
| `rsync_2.6.0`  | 2.6.0   | 27       | 2004; needs autoconf regen (see below)  |
| `rsync_3.0.0`  | 3.0.0   | 30       | 2008                                    |
| `rsync_3.1.0`  | 3.1.0   | 31       | 2013                                    |
| `rsync_3.1.3`  | 3.1.3   | 31       | Ubuntu 18.04 / Debian buster era (2018) |
| `rsync_3.2.0`  | 3.2.0   | 31       | 2020 (zstd/lz4/xxhash negotiation added)|
| `rsync_3.2.7`  | 3.2.7   | 31       | 2022                                    |
| `rsync_3.3.0`  | 3.3.0   | 31       | 2024                                    |
| `rsync_3.4.0`  | 3.4.0   | 32       | 2025                                    |
| `rsync_3.4.1`  | 3.4.1   | 32       | 2025                                    |

These are every `x.y.0` release from 2.6.0 (2004) onward plus a few point
releases. 2.6.0 is the practical floor: older tags need progressively more
porting to build on a current toolchain.

All built `--disable-openssl` and with `_FORTIFY_SOURCE` disabled (see below);
xxhash/zstd/lz4 are compiled in where the version supports them.

## Adding a version

```bash
./build_static.sh 3.2.7            # uses git tag v3.2.7
./build_static.sh 3.0.9 v3.0.9     # explicit tag if naming differs
```

The script checks out the tag into a throwaway `git worktree`, applies the
minimal patches needed to compile old sources on a modern toolchain, links
statically, verifies the result is static and reports the requested version,
then installs `rsync_<version>` here and removes the worktree.

Override the source repo with `RSYNC_REPO=/path/to/rsync ./build_static.sh ...`
(defaults to `../rsync.4`).

## Why the patches?

Modern GCC (>= 14, C23 default) and glibc reject things old rsync relied on.
`build_static.sh` handles these, each guarded so it's a no-op when not needed:

1. **K&R `lseek64()` redeclaration** in `syscall.c` clashes with glibc's real
   prototype — removed.
2. **`gettimeofday()`** — glibc only has the 2-arg form; configure misdetects
   the 1-arg form, so `HAVE_GETTIMEOFDAY_TZ` is forced on in `config.h`.
3. **C23 `()` == `(void)`** breaks K&R prototypes called with arguments
   (`qsort` comparator, `pool->bomb`, etc.) — built with `-std=gnu11`.
4. Assorted modern `-Werror` promotions (incompatible pointer types, implicit
   declarations) downgraded to warnings; bundled zlib/popt used to keep the
   static link self-contained.

5. **OpenSSL (3.2+)** is disabled with `--disable-openssl`: linking
   `libcrypto.a` statically drags in jitterentropy (`jent_*`) and zlib's
   `uncompress` (OpenSSL's COMP module), which don't resolve here. OpenSSL only
   provided optional MD4/MD5, which rsync implements natively, so checksum
   behaviour is unaffected.

6. **`_FORTIFY_SOURCE` disabled** (`-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0`):
   modern Ubuntu defaults it to `=3`, whose stricter object-size checks turn
   latent (historically benign) over-reads in OLD rsync into hard
   `*** buffer overflow detected ***` aborts when the binary runs as a
   server/daemon — which made e.g. 3.1.3 and 3.2.7 unusable as peers. Disabling
   it makes the archival binaries behave as the released versions did.

7. **Pre-3.0 tags (e.g. 2.6.0)** ship `configure.in`, not a generated
   `configure`. The script runs `autoheader`/`autoconf` to generate it, after
   neutralizing the `AC_CHECK_FUNCS(fn,,AC_LIBOBJ(lib/...))` fallbacks for
   `inet_ntop`/`inet_pton`/`getaddrinfo`/`getnameinfo` — modern autoconf emits
   broken shell for those never-taken branches (the funcs exist in glibc). It
   also generates `proto.h` (no make rule in that era) and stubs the vendored
   `lib/addrinfo.h` the tag dropped (modern glibc supplies `struct addrinfo`).
   All guarded so they no-op on 3.x.

Newer versions may need fewer or different tweaks; if a build fails, the
script prints the first compiler errors from its log.
