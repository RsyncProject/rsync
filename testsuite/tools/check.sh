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
pid=
terminate() {
    trap '' TERM
    child=${pid:-${!:-}}
    if test -n "$child"; then
        kill -TERM "$child" 2>/dev/null || :
        wait "$child" 2>/dev/null || :
    fi
    exit 143
}
trap terminate TERM
scratchbase="$output/scratch" python3 -S "$source/testsuite/runtests.py" \
    "$@" \
    --srcdir="$source" --tooldir="$build" --rsync-bin="$binary" \
    --profiles="$profiles" --receipt="$output/receipt.json" \
    <&0 > "$output/run.log" 2>&1 &
pid=$!
wait "$pid" || status=$?
pid=
trap - TERM
cat "$output/run.log"
if ! test -s "$output/receipt.json"; then
    echo "Missing test receipt: $output/receipt.json" >&2
    exit 2
fi
verified=0
python3 -S "$source/testsuite/runtests.py" --check-receipt "$output/receipt.json" \
    --rsync-bin="$binary" --profiles="$profiles" || verified=$?
if test "$verified" -gt 1; then exit "$verified"; fi
if test "$verified" -ne "$status"; then
    echo 'Test receipt disagrees with the runner exit status' >&2
    exit 2
fi
exit "$status"
