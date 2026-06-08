#!/bin/bash
# Build a static rsync binary from a historical git tag, for cross-version
# behaviour testing. Produces ./rsync_<version> in this directory.
#
# Usage:   ./build_static.sh <version> [git-tag]
# Example: ./build_static.sh 3.1.3          # uses tag v3.1.3
#          ./build_static.sh 3.2.7 v3.2.7
#
# Old rsync releases don't compile cleanly on a modern toolchain (GCC >= 14
# defaults to C23, where an empty () prototype means (void); glibc dropped the
# 1-arg gettimeofday; lseek64 K&R redeclarations clash). This script applies
# the minimal, best-effort workarounds and links statically so the result is
# self-contained and reproducible regardless of the host's shared libraries.
#
# Each workaround is guarded so it's a no-op on versions that don't need it.
set -euo pipefail

VERSION="${1:?usage: build_static.sh <version> [git-tag]}"
TAG="${2:-v$VERSION}"

ARCHIVE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="${RSYNC_REPO:-/home/tridge/project/rsync/rsync.4}"   # any rsync worktree
WORKTREE="$(mktemp -d /tmp/rsync-build-XXXXXX)"
OUT="$ARCHIVE_DIR/rsync_$VERSION"

# C standard restores K&R () semantics; permissive flags downgrade the pile of
# modern -Werror promotions (incompatible pointers, implicit decls) to warnings.
# _FORTIFY_SOURCE is forced OFF: modern Ubuntu defaults it to =3, whose stricter
# object-size checks turn latent (historically benign) over-reads in OLD rsync
# into hard "*** buffer overflow detected ***" aborts when the binary acts as a
# server/daemon. Disabling it makes these archival binaries behave the way the
# released versions did, which is the whole point of the archive.
CFLAGS_OLD="-I. -I./zlib -O2 -g -std=gnu11 -fcommon -DHAVE_CONFIG_H -Wno-error \
-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
-Wno-incompatible-pointer-types -Wno-implicit-function-declaration -Wno-int-conversion"

cleanup() {
    cd "$REPO"
    git worktree remove --force "$WORKTREE" 2>/dev/null || true
    git worktree prune 2>/dev/null || true
}
trap cleanup EXIT

echo ">>> checking out $TAG into $WORKTREE"
# prefer an exact tag to avoid ambiguity with similarly-named branches
REF="$TAG"
if git -C "$REPO" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    REF="refs/tags/$TAG"
fi
git -C "$REPO" worktree add --detach "$WORKTREE" "$REF"
cd "$WORKTREE"

# --- workaround 1: K&R lseek64 redeclaration clashes with glibc's prototype ---
if grep -q 'off64_t lseek64();' syscall.c 2>/dev/null; then
    echo ">>> patching syscall.c lseek64 redeclaration"
    perl -0pi -e 's/#ifdef HAVE_LSEEK64\n#if !SIZEOF_OFF64_T\n\tOFF_T lseek64\(\);\n#else\n\toff64_t lseek64\(\);\n#endif\n\treturn lseek64/#ifdef HAVE_LSEEK64\n\treturn lseek64/' syscall.c
fi

# --- workaround 0: pre-3.0 tags ship configure.in, not a generated configure.
# Generate it. Modern autoconf emits broken shell for their
# AC_CHECK_FUNCS(fn,,AC_LIBOBJ(lib/...)) fallbacks -- but those branches are
# dead on a modern host (glibc has inet_ntop/inet_pton/getaddrinfo/getnameinfo),
# so neutralize the AC_LIBOBJ replacements before regenerating.
OLD_TREE=0
if [ ! -f ./configure ] && { [ -f configure.in ] || [ -f configure.ac ]; }; then
    OLD_TREE=1
    acsrc=configure.ac; [ -f configure.in ] && acsrc=configure.in
    echo ">>> generating configure for an old tag (autoheader/autoconf)"
    sed -i 's#AC_LIBOBJ(lib/[a-zA-Z_]*)#:#g' "$acsrc"
    autoheader 2>/dev/null || true
    autoconf 2>/dev/null || { echo "autoconf failed"; exit 1; }
fi

CONF_ARGS=(--disable-md2man --with-included-zlib=yes --with-included-popt=yes)
# OpenSSL (3.2+) only adds optional MD4/MD5 that rsync already implements, but
# linking libcrypto.a statically drags in jitterentropy + zlib's uncompress,
# which aren't resolvable here. Drop it when the flag exists.
if ./configure --help 2>/dev/null | grep -q -- '--disable-openssl'; then
    echo ">>> disabling openssl for self-contained static link"
    CONF_ARGS+=(--disable-openssl)
fi

echo ">>> configure (bundled zlib + popt, static-friendly)"
./configure "${CONF_ARGS[@]}" \
    >"$WORKTREE/conf.log" 2>&1 || { tail -20 "$WORKTREE/conf.log"; exit 1; }

# --- workaround 2: modern glibc only has the 2-arg gettimeofday ---------------
if grep -q '/\* #undef HAVE_GETTIMEOFDAY_TZ \*/' config.h; then
    echo ">>> forcing HAVE_GETTIMEOFDAY_TZ (configure misdetects it)"
    sed -i 's|/\* #undef HAVE_GETTIMEOFDAY_TZ \*/|#define HAVE_GETTIMEOFDAY_TZ 1|' config.h
fi

# --- workaround 4 (old trees only): generate proto.h if the tree has no make
# rule for it, and stub a vendored lib/addrinfo.h that the git tag dropped
# (modern glibc supplies struct addrinfo / sockaddr_storage, so empty is right).
if [ "$OLD_TREE" = 1 ]; then
    if [ ! -f proto.h ] && [ -f mkproto.awk ]; then
        echo ">>> generating proto.h"
        cat ./*.c ./lib/compat.c 2>/dev/null | awk -f ./mkproto.awk > proto.h
    fi
    if grep -q 'include "lib/addrinfo.h"' rsync.h 2>/dev/null && [ ! -f lib/addrinfo.h ]; then
        echo ">>> stubbing lib/addrinfo.h"
        echo '/* emptied: modern glibc provides struct addrinfo */' > lib/addrinfo.h
    fi
fi

echo ">>> building (static)"
make -j"$(nproc)" CFLAGS="$CFLAGS_OLD" LDFLAGS="-static" \
    >"$WORKTREE/make.log" 2>&1 || { grep -E 'error:|\*\*\*' "$WORKTREE/make.log" | head; exit 1; }

# verify it's actually static before we keep it
if ldd ./rsync 2>&1 | grep -qv 'not a dynamic executable'; then
    echo "ERROR: binary is not statically linked:" >&2
    ldd ./rsync >&2
    exit 1
fi

GOT="$(./rsync --version | head -1 | awk '{print $3}')"
if [ "$GOT" != "$VERSION" ]; then
    echo "ERROR: built version '$GOT' != requested '$VERSION'" >&2
    exit 1
fi

cp ./rsync "$OUT"
strip "$OUT"
echo ">>> installed $OUT"
"$OUT" --version | head -1
file "$OUT"
