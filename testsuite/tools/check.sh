#!/bin/sh
set -eu

if test "$#" -lt 4 || test -z "$3"; then
    echo 'Usage: check.sh BUILD_DIR RSYNC PROFILES OUTPUT_DIR [TEST_OPTIONS...]' >&2
    exit 2
fi

source=$(CDPATH= cd "$(dirname "$0")/../.." && pwd -P)
build=$(CDPATH= cd "$1" && pwd -P)
binary=$2
profiles=$3
output=$4
shift 4

case "$binary" in
    /*) ;;
    *) binary=$PWD/$binary ;;
esac
if ! test -f "$binary" || ! test -x "$binary"; then
    echo "Not an executable file: $binary" >&2
    exit 2
fi

mkdir -p "$(dirname "$output")"
mkdir "$output"
output=$(CDPATH= cd "$output" && pwd -P)
mkdir "$output/scratch"
cd "$build"

status=0
unset whichtests
scratchbase="$output/scratch" python3 -S "$source/testsuite/runtests.py" \
    "$@" \
    --srcdir="$source" --tooldir="$build" --rsync-bin="$binary" \
    --profiles="$profiles" --receipt="$output/receipt.json" \
    > "$output/run.log" 2>&1 || status=$?
cat "$output/run.log"
if ! test -s "$output/receipt.json"; then
    echo "Missing test receipt: $output/receipt.json" >&2
    exit 2
fi
exit "$status"
