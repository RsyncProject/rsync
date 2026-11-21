#!/bin/bash
# Build a tagged release for the Linux AMD64 peer suite
# Usage: build_static.sh VERSION [TAG]
# RSYNC_OUTPUT selects a separate output file for qualification
set -euo pipefail

VERSION="${1:?usage: build_static.sh <version> [git-tag]}"
TAG="${2:-v$VERSION}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([a-z]+[0-9]+)?$ ]] || { echo "Invalid version: $VERSION" >&2; exit 2; }
if [ "$(uname -sm)" != 'Linux x86_64' ]; then
    echo 'The archived peer build requires Linux AMD64' >&2
    exit 2
fi

ARCHIVE_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="${RSYNC_REPO:-$(dirname "$ARCHIVE_DIR")}"
SOURCE=$(git -c "safe.directory=$REPO" -C "$REPO" rev-parse --verify "refs/tags/$TAG^{commit}")
OUT="${RSYNC_OUTPUT:-$ARCHIVE_DIR/rsync_$VERSION}"
if [[ -L "$OUT" || ( -e "$OUT" && ! -f "$OUT" ) ]]; then
    echo "Output must be a regular file: $OUT" >&2
    exit 2
fi
pending=
BUILD_DIR="$(mktemp -d /tmp/rsync-build-XXXXXX)"

# Preserve old declarations and unchecked release-era behaviour
CFLAGS_OLD="-I. -I./zlib -O2 -g -std=gnu11 -fcommon -DHAVE_CONFIG_H -Wno-error \
-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 \
-Wno-incompatible-pointer-types -Wno-implicit-function-declaration -Wno-int-conversion"

cleanup() {
    if [ -n "$pending" ]; then rm -f -- "$pending"; fi
    case "$BUILD_DIR" in
        /tmp/rsync-build-*)
            [ "$(realpath "$BUILD_DIR")" = "$BUILD_DIR" ] && rm -rf -- "$BUILD_DIR" ;;
        *) echo "Unexpected build directory: $BUILD_DIR" >&2 ;;
    esac
}
trap cleanup EXIT

echo "Building $TAG at $SOURCE"
git -c "safe.directory=$REPO" -C "$REPO" archive "$SOURCE" | tar -x -C "$BUILD_DIR"
cd "$BUILD_DIR"

# Modern headers already declare lseek64
if grep -q 'off64_t lseek64();' syscall.c 2>/dev/null; then
    echo ">>> patching syscall.c lseek64 redeclaration"
    perl -0pi -e 's/#ifdef HAVE_LSEEK64\n#if !SIZEOF_OFF64_T\n\tOFF_T lseek64\(\);\n#else\n\toff64_t lseek64\(\);\n#endif\n\treturn lseek64/#ifdef HAVE_LSEEK64\n\treturn lseek64/' syscall.c
fi

# Regenerate the old Autoconf inputs without obsolete libc replacements
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
# Use the bundled hashes for the static peer build
help=$(./configure --help)
if [[ "$help" == *--disable-openssl* ]]; then
    echo "Using bundled hashes"
    CONF_ARGS+=(--disable-openssl)
fi
if [[ "$help" == *--disable-idn* ]]; then
    CONF_ARGS+=(--disable-idn)
fi

echo ">>> configure (bundled zlib + popt, static-friendly)"
./configure "${CONF_ARGS[@]}" \
    >"$BUILD_DIR/conf.log" 2>&1 || { tail -20 "$BUILD_DIR/conf.log"; exit 1; }

# Modern glibc requires the second gettimeofday argument
if grep -q '/\* #undef HAVE_GETTIMEOFDAY_TZ \*/' config.h; then
    echo ">>> forcing HAVE_GETTIMEOFDAY_TZ (configure misdetects it)"
    sed -i 's|/\* #undef HAVE_GETTIMEOFDAY_TZ \*/|#define HAVE_GETTIMEOFDAY_TZ 1|' config.h
fi

# Old tags need generated prototypes and current libc address declarations
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
make -j8 CFLAGS="$CFLAGS_OLD" LDFLAGS="-static" \
    >"$BUILD_DIR/make.log" 2>&1 || { grep -E 'error:|\*\*\*' "$BUILD_DIR/make.log" | head; exit 1; }

elf=$(readelf -h -l -d ./rsync)
[[ "$elf" == *'Advanced Micro Devices X86-64'* ]]
if [[ "$elf" == *INTERP* || "$elf" == *NEEDED* ]]; then
    echo "Binary is not statically linked" >&2
    exit 1
fi
banner=$(timeout -k 2 10 ./rsync --version)
GOT=$(printf '%s\n' "$banner" | awk 'NR == 1 {print $3}')
if [ "$GOT" != "$VERSION" ]; then
    echo "Built version '$GOT' differs from '$VERSION'" >&2
    exit 1
fi

mkdir smoke-source smoke-local smoke-daemon
printf 'peer transfer\n' > smoke-source/file
timeout -k 2 30 ./rsync -r smoke-source/ smoke-local/
cmp smoke-source/file smoke-local/file
printf 'use chroot = no\nuid = %s\ngid = %s\nlog file = %s\n[module]\npath = %s\nread only = no\n' \
    "$(id -u)" "$(id -g)" "$BUILD_DIR/smoke.log" "$BUILD_DIR/smoke-daemon" > smoke.conf
RSYNC_CONNECT_PROG="'$BUILD_DIR/rsync' --daemon --config='$BUILD_DIR/smoke.conf'" \
    timeout -k 2 30 ./rsync -r smoke-source/ rsync://localhost/module/
cmp smoke-source/file smoke-daemon/file

pending=$(mktemp "$OUT.XXXXXX")
install -m 755 ./rsync "$pending"
strip "$pending"
mv -T -- "$pending" "$OUT"
pending=
printf '%s\n' "$banner"
sha256sum "$OUT"
