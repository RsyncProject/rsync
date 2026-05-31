# fuzz/Makefile - libFuzzer harnesses for rsync's UNTRUSTED-PEER wire parsers.
#
# This is the ONLY build-system addition in the fuzzing workstream. The
# top-level Makefile.in is untouched. The wire-parser objects (io.o, token.o,
# ...) are produced by the normal rsync build, which must have been configured
# with the campaign sanitizer CFLAGS so those objects are instrumented:
#
#   CFLAGS="-g -O1 -fsanitize=fuzzer-no-link,address,undefined -fno-omit-frame-pointer" \
#     CC=clang ./configure --disable-md2man
#   make io.o token.o            # (configure preserves env CFLAGS)
#
# Then build the harnesses:
#
#   make -C fuzz                 # builds every harness
#   make -C fuzz fuzz_io         # one harness
#
# Run regression mode (bounded, exits non-zero on crash):
#
#   make -C fuzz regression
#   ./fuzz/run-regression.sh     # equivalent standalone runner
#
# Inside the nix shell, prefix with:
#   nix develop path:$HOME/git/rsync --command bash -c 'make -C fuzz ...'

CC ?= clang
RSYNC_DIR := ..

# Harnesses LINK with -fsanitize=fuzzer (provides main + the coverage runtime);
# address,undefined match the instrumentation baked into the rsync objects.
FUZZ_SAN := -fsanitize=fuzzer,address,undefined
# Compiling our own harness/stub TUs: instrument them too, but no libFuzzer main.
HARNESS_SAN := -fsanitize=fuzzer-no-link,address,undefined -fno-omit-frame-pointer

CPPFLAGS := -I$(RSYNC_DIR) -I$(RSYNC_DIR)/zlib -DHAVE_CONFIG_H
HARNESS_CFLAGS := -g -O1 -std=gnu23 $(HARNESS_SAN) $(CPPFLAGS)

# Each harness links the named rsync object(s) + the shared stubs.
# io.o is enough for fuzz_io; token.o additionally needs io.o + zlib + (because
# token.o references them even on the CPRES_NONE path) liblz4 / libzstd.
IO_OBJS    := $(RSYNC_DIR)/io.o
# rsync bundles its own zlib objects (used by recv_deflated_token); link them
# even though fuzz_token currently drives only the CPRES_NONE/simple path, so
# the object resolves all references.
ZLIB_OBJS  := $(RSYNC_DIR)/zlib/deflate.o $(RSYNC_DIR)/zlib/inffast.o \
              $(RSYNC_DIR)/zlib/inflate.o $(RSYNC_DIR)/zlib/inftrees.o \
              $(RSYNC_DIR)/zlib/trees.o $(RSYNC_DIR)/zlib/zutil.o \
              $(RSYNC_DIR)/zlib/adler32.o $(RSYNC_DIR)/zlib/compress.o \
              $(RSYNC_DIR)/zlib/crc32.o
TOKEN_OBJS := $(RSYNC_DIR)/token.o $(RSYNC_DIR)/io.o $(ZLIB_OBJS)
TOKEN_LIBS := -llz4 -lzstd

HARNESSES := fuzz_io fuzz_token
# fuzz_recv_discard drives the receiver discard-path NULL-deref (static-findings
# REAL #1). It needs the real read_sum_head/recv_token (io.o + token.o + zlib)
# plus the real full_fname (util1.o), where the NULL deref actually lands.
RECV_DISCARD_OBJS := $(RSYNC_DIR)/util1.o $(TOKEN_OBJS)
RECV_DISCARD_LIBS := -llz4 -lzstd

HARNESSES := fuzz_io fuzz_token fuzz_recv_discard

.PHONY: all clean regression
all: $(HARNESSES)

stubs.o: stubs.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

fuzz_io.o: fuzz_io.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

fuzz_io: fuzz_io.o stubs.o $(IO_OBJS)
	$(CC) $(FUZZ_SAN) $^ -o $@

fuzz_token.o: fuzz_token.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

fuzz_token: fuzz_token.o stubs.o $(TOKEN_OBJS)
	$(CC) $(FUZZ_SAN) $^ -o $@ $(TOKEN_LIBS)

fuzz_recv_discard.o: fuzz_recv_discard.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

recv_discard_stubs.o: recv_discard_stubs.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

fuzz_recv_discard: fuzz_recv_discard.o stubs.o $(RECV_DISCARD_OBJS) recv_discard_stubs.o
	# util1.o provides real definitions of a few helpers (flist_ndx_push/pop,
	# glob_expand*) that stubs.c also stubs; none are on the full_fname path, so
	# allow the duplicates (linker keeps the first / stubs.c version).
	$(CC) $(FUZZ_SAN) -Wl,--allow-multiple-definition $^ -o $@ $(RECV_DISCARD_LIBS)

regression: all
	./run-regression.sh

clean:
	rm -f $(HARNESSES) *.o crash-* leak-* timeout-* oom-*
