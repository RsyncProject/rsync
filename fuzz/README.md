# Fuzzing harnesses (Workstream 2)

libFuzzer harnesses for rsync's **UNTRUSTED-PEER wire parsers**, built so the
existing `read_*_bounded` / `MAX_WIRE_*` guards are actually exercised under
ASan + UBSan. A guard that correctly rejects a hostile value unwinds cleanly; a
genuine out-of-bounds access trips the sanitizer *before* any guard runs — so a
crash here means a real parser bug, not a harness artifact.

## Targets

| Harness        | Surface                                   | Status |
|----------------|-------------------------------------------|--------|
| `fuzz_io`      | `io.c` primitives: `read_sum_head`, `read_varint`/`read_varlong`/`read_longint`/`read_vstring`, `read_int_bounded`/`read_varint_bounded`/`read_varint_size` | **working** |
| `fuzz_token`   | `token.c` `recv_token` → `simple_recv_token` (CPRES_NONE literal-token path; the `i > CHUNK_SIZE` guard) | **working** |
| `fuzz_flist`   | `flist.c` `recv_file_entry`                | staged stub (see header notes) |
| `fuzz_xattrs`  | `xattrs.c` `receive_xattr`                 | staged stub (see header notes) |

The two stubs are no-op `LLVMFuzzerTestOneInput`s with a detailed header
documenting the exact target region, the required global-init contract, and the
dependency wall that puts a *hygienic* harness out of WS2 budget. They are not
wired into the Makefile and never run in regression.

## Toolchain

clang (libFuzzer) + ASan/UBSan + compiler-rt, gcc, make, python3 are all
provided by the project nix shell:

```sh
nix develop path:$HOME/git/rsync --command bash -c '<cmds>'
```

clang is **not** committed into the repo (the fork stays upstreamable).

## Build

The wire-parser objects are produced by the *normal* rsync build, configured
with the campaign sanitizer CFLAGS so those objects are instrumented (configure
preserves env `CFLAGS` — **no `Makefile.in` change is needed**):

```sh
nix develop path:$HOME/git/rsync --command bash -c '
  ./prepare-source &&
  CFLAGS="-g -O1 -fsanitize=fuzzer-no-link,address,undefined -fno-omit-frame-pointer" \
    CC=clang ./configure --disable-md2man &&
  make io.o token.o \
       zlib/inflate.o zlib/inftrees.o zlib/inffast.o zlib/zutil.o \
       zlib/adler32.o zlib/deflate.o zlib/trees.o zlib/compress.o zlib/crc32.o &&
  make -C fuzz'
```

`make -C fuzz` builds every working harness (one `-fsanitize=fuzzer` link rule
each — the **only** build-system addition in this workstream). `make -C fuzz
fuzz_io` builds just one.

## Run / regression mode (what WS3 CI calls)

```sh
nix develop path:$HOME/git/rsync --command ./fuzz/run-regression.sh
```

`run-regression.sh` (equivalently `make -C fuzz regression`) builds each working
harness, deterministically replays its committed seed corpus (`-runs=0`), then
does a **bounded** top-up fuzz run (`-max_total_time`, default 30s) seeded from
that corpus. It **exits non-zero on any crash**. Knobs: `FUZZ_MAX_TIME`,
`FUZZ_TARGETS`.

## Linking / stubbing strategy

rsync is not a library, so each harness links a curated set of *unmodified*
rsync `.o` files plus `fuzz/stubs.c`. **No tracked rsync source is modified.**

- **`fuzz_io`** = `fuzz_io.o` + `stubs.o` + `io.o`.
- **`fuzz_token`** = `fuzz_token.o` + `stubs.o` + `token.o` + `io.o` +
  rsync's bundled `zlib/*.o` + `-llz4 -lzstd` (token.o references LZ4/ZSTD
  symbols even though the CPRES_NONE path never calls them; the system libs
  resolve them).

`fuzz/stubs.c` supplies everything the objects reference that we do not want to
drag in:

- **`_exit_cleanup` / `_out_of_memory` / `_overflow_exit` → `longjmp`** back to
  the harness. This is the load-bearing shim: a wire-range guard calls
  `exit_cleanup(RERR_*)` on a malformed value; we turn that into a clean unwind
  so a *correctly rejected* input is not a crash. A real memory bug trips
  ASan/UBSan *before* the guard, preserving oracle fidelity.
- **Logging no-ops**: `rprintf`, `rsyserr`, `rwrite`, `who_am_i`, `do_big_num`.
- **A self-contained `my_alloc`** (honours `max_alloc`, returns NULL on the
  `file==NULL` over-limit path that `EXPAND_ITEM_LIST` relies on) so ASan tracks
  every wire-driven allocation.
- **Real zero-filled `info_levels` / `debug_levels`** arrays (the `INFO_GTE` /
  `DEBUG_GTE` macros index them directly; NULL would crash).
- **Default-valued globals** referenced by the objects (`stats`, `am_*`,
  `io_error`, `do_compression`, `module_id`, …) and **no-op shims** for
  functions only reached on code paths the parsers never enter
  (`match_hard_links`, `successful_send`, `glob_expand`, `recv_file_list`, …).

To rediscover the exact symbol set after a rebase:
`nm -u io.o | sed 's/.* U //' | sort` (and likewise for `token.o`); anything not
in libc is either a default global or a no-op shim in `stubs.c`.

## Global-init contract

Per `reference.md` Part 3.5, the harness controls the minimal global state so a
crash is a real parser bug:

- `protocol_version` — `fuzz_io` derives it per-input from byte 0 (cycling
  20/26/29/30/31) to cover the `proto<27`/`<30`/`>=30` branches of
  `read_sum_head` and the `varint30` width choices; `fuzz_token` pins 30.
- `xfer_sum_len` — `fuzz_io` derives it per-input (4..32); bounds `s2length` and
  feeds the multiply-overflow guard in `read_sum_head`.
- `do_compression = CPRES_NONE` in `fuzz_token` (selects `simple_recv_token`).
- The iobuf is left at its default (`.in_fd = -1`). That is the whole trick:
  `read_buf(f, …)` takes its `f != iobuf.in_fd` fast path straight to
  `safe_read(f, …)`, so the readers pull bytes directly from a fd we control —
  no multiplexing, no msg framing — with the bytes 100% attacker-chosen.

The fuzz buffer reaches the readers via a **pipe**: write all bytes, close the
write end; reads drain the buffer then hit EOF, which `read_buf` turns into
`whine_about_eof → exit_cleanup → longjmp`. "Ran out of bytes" is therefore a
clean unwind, never a crash.

## Seed corpus

`corpus/<target>/` holds descriptively-named seeds (hand-built valid, boundary,
and over-range cases). libFuzzer-generated entries (40-hex-char names) are
git-ignored; a genuine crash repro should be minimized and committed under
`corpus/<target>/` with a descriptive name.

- **`fuzz_io`** (byte 0 selects protocol/sum_len): valid sum_head (proto 30 and
  proto 20), empty (count 0), `blength` exactly at `MAX_BLOCK_SIZE` and one
  over, negative count, huge count (multiply-overflow guard), `s2length` over
  `xfer_sum_len`, `remainder > blength`, a mixed varint+vstring stream, and an
  all-`0xff` stress case (drives the `read_longint` 64-bit sentinel).
- **`fuzz_token`**: a small literal token, a zero/negative (match) token, a
  literal at exactly `CHUNK_SIZE`, one over `CHUNK_SIZE` (the guard), a huge
  length, and a truncated literal (EOF unwind).

Lengths/encodings are little-endian `read_int` and follow the exact wire layout
of `read_sum_head` / `simple_recv_token`.

## Verification result

`fuzz_io` and `fuzz_token` both **build and run clean** under ASan+UBSan over
their seed corpora plus millions of generated inputs. No crashes were found in
the io.c or token.c (simple path) wire guards — i.e. the existing bounds hold up
under fuzzing.
