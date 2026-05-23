#!/bin/sh
# Cross-build a statically-linked rsync for Termux and package it as a .deb.
#
# Usage: packaging/build-termux-deb.sh <termux-arch> [api-level] [outdir]
#   <termux-arch>   aarch64 | arm | x86_64 | i686
#   [api-level]     Android API level to target (default 24 = Android 7.0)
#   [outdir]        where to write the .deb (default ./dist)
#
# Requirements:
#   * Android NDK, located via $ANDROID_NDK_LATEST_HOME or $ANDROID_NDK_ROOT
#   * dpkg-deb, autoconf, automake, gawk
#   * run from a clean rsync git checkout (it builds in-tree)
#
# The result is a self-contained static binary installed under the Termux
# prefix (/data/data/com.termux/files/usr/bin/rsync), so it needs no other
# Termux packages at runtime. Install on a device with:
#   dpkg -i rsync_<ver>_<arch>.deb      (or: apt install ./rsync_<ver>_<arch>.deb)

set -e

arch=$1
API=${2:-24}
OUTDIR=${3:-"$PWD/dist"}

if [ -z "$arch" ]; then
    echo "usage: $0 <aarch64|arm|x86_64|i686> [api] [outdir]" >&2
    exit 2
fi

case "$arch" in
    aarch64) triple=aarch64-linux-android ;;
    arm)     triple=armv7a-linux-androideabi ;;
    x86_64)  triple=x86_64-linux-android ;;
    i686)    triple=i686-linux-android ;;
    *) echo "unknown Termux arch: $arch" >&2; exit 2 ;;
esac

NDK=${ANDROID_NDK_LATEST_HOME:-$ANDROID_NDK_ROOT}
if [ -z "$NDK" ] || [ ! -d "$NDK" ]; then
    echo "Android NDK not found (set ANDROID_NDK_LATEST_HOME or ANDROID_NDK_ROOT)" >&2
    exit 2
fi
TC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"

CC="$TC/${triple}${API}-clang"
if [ ! -x "$CC" ]; then
    echo "no NDK compiler for $triple at API $API: $CC" >&2
    exit 2
fi
export CC
export AR="$TC/llvm-ar" RANLIB="$TC/llvm-ranlib" STRIP="$TC/llvm-strip"
export CFLAGS="-O2" LDFLAGS="-static"

# Cross-compile cache values that configure cannot probe by running a test:
#   - lchmod()/lutimes() link but aren't declared by Bionic until API 36, so
#     force them off and let rsync use its fallbacks;
#   - socketpair and mknod-FIFO/socket are present (Android runs a Linux
#     kernel), so restore the values the run-tests would have found.
export ac_cv_func_lchmod=no ac_cv_func_lutimes=no \
       rsync_cv_HAVE_SOCKETPAIR=yes \
       rsync_cv_MKNOD_CREATES_FIFOS=yes \
       rsync_cv_MKNOD_CREATES_SOCKETS=yes

echo "=== configure ($arch, API $API) ==="
./configure --host="$triple" --build=x86_64-pc-linux-gnu --enable-ipv6 \
    --disable-zstd --disable-lz4 --disable-xxhash --disable-openssl \
    --disable-iconv --disable-iconv-open --disable-acl-support \
    --disable-xattr-support --disable-md2man --disable-roll-simd \
    --with-included-popt --with-included-zlib

# Generate the awk-built headers serially first so the parallel build can't
# race on proto.h <- daemon-parm.h.
make proto.h
echo "=== build ($arch) ==="
make -j"$(nproc)" rsync
"$STRIP" rsync

VER=$(sed -n 's/.*RSYNC_VERSION "\([^"]*\)".*/\1/p' version.h)
echo "=== package rsync $VER for Termux/$arch ==="

pkg=$(mktemp -d)
trap 'rm -rf "$pkg"' EXIT
chmod 755 "$pkg"   # mktemp makes 0700; the package root should be world-readable
install -Dm755 rsync "$pkg/data/data/com.termux/files/usr/bin/rsync"
size=$(du -ks "$pkg/data" | cut -f1)

mkdir -p "$pkg/DEBIAN"
cat > "$pkg/DEBIAN/control" <<EOF
Package: rsync
Version: $VER
Architecture: $arch
Maintainer: rsync project <rsync@lists.samba.org>
Installed-Size: $size
Homepage: https://rsync.samba.org/
Section: net
Priority: optional
Description: fast, versatile file-copying tool (static Termux build)
 Statically linked rsync, cross-compiled from the rsync git tree with the
 Android NDK for use on Termux. It has no external dependencies; the optional
 zstd/lz4/xxhash/openssl/acl/xattr/iconv features are omitted in favour of a
 single self-contained binary (md5/md4 checksums and bundled zlib remain).
EOF

mkdir -p "$OUTDIR"
deb="$OUTDIR/rsync_${VER}_${arch}.deb"
dpkg-deb --root-owner-group --build "$pkg" "$deb"
echo "built $deb"
