# Fuzz Testing for rsync

This directory contains [libFuzzer](https://llvm.org/docs/LibFuzzer.html) fuzz
targets for rsync. These are also used by
[OSS-Fuzz](https://github.com/google/oss-fuzz) for continuous fuzzing.

## Fuzz Targets

| Target | Description |
|---|---|
| `fuzz_wildmatch` | Pattern matching via `wildmatch()` / `iwildmatch()` |
| `fuzz_parse_filter` | Filter rule parsing via `parse_filter_str()` |
| `fuzz_rsyncd_conf` | rsyncd.conf config parsing via `lp_load()` |

## Building

```bash
# From the rsync root directory:
./configure --disable-md2man --disable-xxhash --disable-zstd --disable-lz4 \
    --with-included-popt \
    CC=clang CFLAGS="-g -O1 -fsanitize=fuzzer-no-link,address"
make

# Build fuzz targets:
make -C fuzz
```

## Running

```bash
# Run with a dictionary for better coverage:
./fuzz/fuzz_wildmatch -dict=fuzz/wildmatch.dict corpus_wildmatch/
./fuzz/fuzz_parse_filter -dict=fuzz/filter_rules.dict corpus_filter/
./fuzz/fuzz_rsyncd_conf -dict=fuzz/rsyncd_conf.dict corpus_conf/
```

## Sanitizers

The default build uses AddressSanitizer. You can also use other sanitizers:

```bash
# UndefinedBehaviorSanitizer:
./configure ... CFLAGS="-g -O1 -fsanitize=fuzzer-no-link,undefined"

# MemorySanitizer (requires msan-instrumented libc):
./configure ... CFLAGS="-g -O1 -fsanitize=fuzzer-no-link,memory"
```
