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
TARGETS="${FUZZ_TARGETS:-fuzz_io}"

# Ensure the rsync wire-parser objects exist & are sanitizer-instrumented.
# (CI is expected to have configured with the campaign CFLAGS already.)
make -C .. io.o >/dev/null

make all

rc=0
for t in $TARGETS; do
	echo "=== regression: $t ==="
	corpus="corpus/$t"
	mkdir -p "$corpus"
	# 1) Deterministic replay of every committed seed (runs=0 => just the corpus).
	if ! ./"$t" -runs=0 "$corpus"; then
		echo "FAIL: $t crashed replaying seed corpus" >&2
		rc=1
		continue
	fi
	# 2) Bounded top-up fuzz seeded from the corpus; any crash file => fail.
	if ! ./"$t" -max_total_time="$MAX_TIME" -print_final_stats=0 "$corpus"; then
		echo "FAIL: $t crashed during bounded fuzz run" >&2
		rc=1
	fi
done

if [ "$rc" -eq 0 ]; then
	echo "All fuzz regression targets clean."
fi
exit "$rc"
