#!/bin/sh
# fuzz/run-regression.sh - CI regression mode (Workstream 3 calls this).
#
# Builds every harness and replays its committed seed corpus under
# ASan/UBSan for a BOUNDED time. Exits NON-ZERO on the first crash so CI
# fails on any regression. It does NOT fuzz open-endedly; it is a fast,
# deterministic gate (libFuzzer in corpus-replay mode + a short top-up run).
#
# Run inside the nix shell that provides clang/libFuzzer:
#   nix develop path:$HOME/git/rsync --command ./fuzz/run-regression.sh
#
# Env knobs:
#   FUZZ_MAX_TIME   seconds of top-up fuzzing per target after replay (default 30)
#   FUZZ_TARGETS    space-separated subset of targets (default: all built)

set -eu

cd "$(dirname "$0")"

MAX_TIME="${FUZZ_MAX_TIME:-30}"
TARGETS="${FUZZ_TARGETS:-fuzz_io fuzz_token fuzz_recv_discard fuzz_deflated_token fuzz_flist fuzz_xattrs}"

# Ensure the rsync wire-parser objects exist & are sanitizer-instrumented.
# (CI is expected to have configured with the campaign CFLAGS already.)
# io.o feeds fuzz_io; token.o feeds fuzz_token; util1.o (real full_fname) feeds
# fuzz_recv_discard's discard-path regression; the flist/xattrs harnesses pull
# in a broad real call graph, so build those objects too.
make -C .. io.o token.o util1.o util2.o uidlist.o exclude.o hashtable.o checksum.o \
	syscall.o acls.o xattrs.o fileio.o chmod.o \
	lib/wildmatch.o lib/compat.o lib/snprintf.o lib/mdfour.o lib/md5.o \
	lib/permstring.o lib/pool_alloc.o lib/sysacls.o lib/sysxattrs.o >/dev/null

make all

# Narrow LSan suppressions for fuzz_flist / fuzz_xattrs: those harnesses drive
# recv_file_entry / receive_xattr, which populate two intentional
# process-lifetime static caches (flist.c `lastdir`, xattrs.c `rsync_xal_l`).
# Those are by-design and cannot be freed from a receive-only harness, so LSan
# would otherwise report them on every run and drown a real new leak. The
# suppressions name ONLY those two allocation-site functions (not a blanket
# detect_leaks=0), so any OTHER leak still fails the run. See lsan-suppressions.txt.
LSAN_SUPP="$(pwd)/lsan-suppressions.txt"

rc=0
for t in $TARGETS; do
	echo "=== regression: $t ==="
	corpus="corpus/$t"
	mkdir -p "$corpus"
	# Only the flist/xattrs harnesses touch the documented static caches.
	case "$t" in
	fuzz_flist|fuzz_xattrs) tlsan="suppressions=$LSAN_SUPP" ;;
	*) tlsan="" ;;
	esac
	# 1) Deterministic replay of every committed seed (runs=0 => just the corpus).
	if ! LSAN_OPTIONS="$tlsan" ./"$t" -runs=0 "$corpus"; then
		echo "FAIL: $t crashed replaying seed corpus" >&2
		rc=1
		continue
	fi
	# 2) Bounded top-up fuzz seeded from the corpus; any crash file => fail.
	if ! LSAN_OPTIONS="$tlsan" ./"$t" -max_total_time="$MAX_TIME" -print_final_stats=0 "$corpus"; then
		echo "FAIL: $t crashed during bounded fuzz run" >&2
		rc=1
	fi
done

if [ "$rc" -eq 0 ]; then
	echo "All fuzz regression targets clean."
fi
exit "$rc"
