#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd -P)
if ! test -f "$root/.github/check-results.jq"; then
    echo 'CI result checker is missing' >&2
    exit 1
fi
scratch=$(mktemp -d)
trap 'rm -f "$scratch/out" "$scratch/err"; rmdir "$scratch"' 0
export CI_EXPECT_LINUX=true CI_EXPECT_DIAGNOSTICS=true CI_EXPECT_BSD=false
data=$(jq -n '{
    linux: {result: "success", outputs: {
        jobs: ({ubuntu: {result: "success"}, almalinux: {result: "skipped"}} | tojson),
        inputs: ({ubuntu: true, almalinux: false} | tojson)}},
    diagnostics: {result: "success", outputs: {
        jobs: ({scan_pinned: {result: "success"}, scan_latest: {result: "failure"}, asan: {result: "skipped"}} | tojson),
        inputs: ({scan_pinned: true, scan_latest: true, asan: false} | tojson), advisory: "scan_latest"}},
    bsd: {result: "skipped", outputs: {}}
}')

check() {
    expected=$1
    edit=$2
    payload=$(printf '%s\n' "$data" | jq "$edit")
    actual=fail
    if printf '%s\n' "$payload" | jq -er -f "$root/.github/check-results.jq" >"$scratch/out" 2>"$scratch/err"; then
        actual=pass
    fi
    if test "$actual" != "$expected"; then
        echo "Unexpected CI result for: $edit" >&2
        cat "$scratch/out" "$scratch/err" >&2
        exit 1
    fi
}

check pass '.'
check pass '.diagnostics.outputs.jobs |= (fromjson | .scan_latest.result = "success" | tojson)'
CI_EXPECT_BSD=true check pass '.bsd = {result: "success", outputs: {jobs: ({test: {result: "success"}} | tojson)}}'
check fail 'del(.linux)'
check fail '.extra = {result: "success", outputs: {}}'
check fail '.linux.result = "failure"'
check fail '.linux.result = "cancelled"'
check fail '.linux.result = "skipped"'
check fail '.bsd.result = "success"'
check fail '.linux.outputs.jobs = "{}"'
check fail '.linux.outputs.jobs = "[]"'
check fail '.linux.outputs.jobs = "not JSON"'
check fail 'del(.linux.outputs.jobs)'
check fail '.linux.outputs.inputs = "[]"'
check fail '.linux.outputs.inputs = "not JSON"'
check fail 'del(.linux.outputs.inputs)'
check fail '.linux.outputs.inputs |= (fromjson | .unknown = true | tojson)'
check fail '.linux.outputs.inputs |= (fromjson | .ubuntu = "true" | tojson)'
check fail '.linux.outputs.inputs |= (fromjson | .ubuntu = false | tojson)'
check fail '.linux.outputs.inputs |= (fromjson | .ubuntu = false | tojson) | .linux.outputs.jobs |= (fromjson | .ubuntu.result = "skipped" | tojson)'
check fail '.linux.outputs.jobs |= (fromjson | .ubuntu.result = "failure" | tojson)'
check fail '.linux.outputs.jobs |= (fromjson | .ubuntu.result = "skipped" | tojson)'
check fail '.linux.outputs.jobs |= (fromjson | .ubuntu.result = "cancelled" | tojson)'
check fail '.linux.outputs.jobs |= (fromjson | del(.ubuntu) | tojson)'
check fail '.linux.outputs.jobs |= (fromjson | .almalinux.result = "success" | tojson)'
check fail '.diagnostics.outputs.advisory = ""'
check fail '.diagnostics.outputs.advisory = "unknown"'
check fail '.diagnostics.outputs.jobs |= (fromjson | .scan_latest.result = "cancelled" | tojson)'
check fail '.diagnostics.outputs.jobs |= (fromjson | .scan_latest.result = "skipped" | tojson)'
check fail '.bsd.result = "cancelled"'
check fail '{}'
export CI_EXPECT_LINUX=false CI_EXPECT_DIAGNOSTICS=false
check fail 'with_entries(.value = {result: "skipped", outputs: {}})'
echo 'CI result checks passed'
