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

# fuzz_recv_discard drives the receiver discard-path NULL-deref (static-findings
# REAL #1). It needs the real read_sum_head/recv_token (io.o + token.o + zlib)
# plus the real full_fname (util1.o), where the NULL deref actually lands.
RECV_DISCARD_OBJS := $(RSYNC_DIR)/util1.o $(TOKEN_OBJS)
RECV_DISCARD_LIBS := -llz4 -lzstd

# fuzz_deflated_token: drives recv_deflated_token (zlib/CPRES_ZLIB path), which
# is a file-static. token_fuzz.o is the REAL token.c recompiled with the in-file
# fuzz hook enabled (-DRSYNC_FUZZ_TOKEN) - same parser/inflate/bounds code, plus
# the thin reset+entry wrappers. Links the REAL io.o + REAL bundled zlib.
DEFL_TOKEN_OBJS := $(RSYNC_DIR)/token_fuzz.o $(RSYNC_DIR)/io.o $(ZLIB_OBJS)
DEFL_TOKEN_LIBS := -llz4 -lzstd

# fuzz_flist: links the REAL flist.o (compiled -DRSYNC_FUZZ_FLIST as flist_fuzz.o)
# plus its whole reachable call graph. The fuzz wrappers live inside flist.c
# under #ifdef RSYNC_FUZZ_FLIST, so we build a dedicated flist_fuzz.o.
# The whole reachable call graph of recv_file_entry / receive_xattr, linked
# REAL & instrumented. REAL_CORE excludes xattrs.o so each harness can pick the
# plain (fuzz_flist) or -DRSYNC_FUZZ_XATTRS (fuzz_xattrs) build of it.
REAL_CORE := $(RSYNC_DIR)/io.o $(RSYNC_DIR)/util1.o $(RSYNC_DIR)/util2.o \
             $(RSYNC_DIR)/uidlist.o $(RSYNC_DIR)/exclude.o \
             $(RSYNC_DIR)/hashtable.o $(RSYNC_DIR)/checksum.o \
             $(RSYNC_DIR)/syscall.o $(RSYNC_DIR)/acls.o \
             $(RSYNC_DIR)/fileio.o \
             $(RSYNC_DIR)/lib/wildmatch.o $(RSYNC_DIR)/lib/compat.o \
             $(RSYNC_DIR)/lib/snprintf.o $(RSYNC_DIR)/lib/mdfour.o \
             $(RSYNC_DIR)/lib/md5.o $(RSYNC_DIR)/lib/permstring.o \
             $(RSYNC_DIR)/lib/pool_alloc.o $(RSYNC_DIR)/lib/sysacls.o \
             $(RSYNC_DIR)/lib/sysxattrs.o $(RSYNC_DIR)/chmod.o
FLIST_OBJS := $(RSYNC_DIR)/flist_fuzz.o globals.o $(RSYNC_DIR)/xattrs.o $(REAL_CORE)
FLIST_LIBS := -lcrypto -lxxhash -lacl

# fuzz_xattrs: receive_xattr lives in xattrs.o; storage tail pulls checksum.o +
# hashtable.o + acls/exclude. Built as xattrs_fuzz.o (#ifdef RSYNC_FUZZ_XATTRS).
# receive_xattr's storage tail (rsync_xal_store -> hashtable + checksum) and
# f_name reach into flist.o, so we link flist_fuzz.o (which also supplies the
# globals flist.c owns) alongside the same real core.
XATTRS_OBJS := $(RSYNC_DIR)/xattrs_fuzz.o $(RSYNC_DIR)/flist_fuzz.o globals.o $(REAL_CORE)
XATTRS_LIBS := -lcrypto -lxxhash -lacl

HARNESSES := fuzz_io fuzz_token fuzz_recv_discard fuzz_deflated_token fuzz_flist fuzz_xattrs

.PHONY: all clean regression
all: $(HARNESSES)

stubs.o: stubs.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

globals.o: globals.c
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

$(RSYNC_DIR)/token_fuzz.o: $(RSYNC_DIR)/token.c
	$(CC) $(RSYNC_CFLAGS) -DRSYNC_FUZZ_TOKEN -c $< -o $@

fuzz_deflated_token.o: fuzz_deflated_token.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

fuzz_deflated_token: fuzz_deflated_token.o stubs.o $(DEFL_TOKEN_OBJS)
	$(CC) $(FUZZ_SAN) $^ -o $@ $(DEFL_TOKEN_LIBS)

# flist_fuzz.o / xattrs_fuzz.o are the REAL flist.c / xattrs.c recompiled with
# the in-file fuzz hook enabled (#ifdef RSYNC_FUZZ_*). Same sanitizer CFLAGS as
# the rest of the instrumented rsync objects; -DRSYNC_FUZZ_* only adds the thin
# wrappers, it does not alter parser code.
RSYNC_CFLAGS := -std=gnu23 -I$(RSYNC_DIR) -I$(RSYNC_DIR)/zlib -g -O1 \
                -fsanitize=fuzzer-no-link,address,undefined -fno-omit-frame-pointer \
                -DHAVE_CONFIG_H -Wall -W

$(RSYNC_DIR)/flist_fuzz.o: $(RSYNC_DIR)/flist.c
	$(CC) $(RSYNC_CFLAGS) -DRSYNC_FUZZ_FLIST -c $< -o $@

$(RSYNC_DIR)/xattrs_fuzz.o: $(RSYNC_DIR)/xattrs.c
	$(CC) $(RSYNC_CFLAGS) -DRSYNC_FUZZ_XATTRS -c $< -o $@

fuzz_flist.o: fuzz_flist.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

fuzz_flist: fuzz_flist.o stubs.o $(FLIST_OBJS)
	$(CC) $(FUZZ_SAN) $^ -o $@ $(FLIST_LIBS)

fuzz_xattrs.o: fuzz_xattrs.c
	$(CC) $(HARNESS_CFLAGS) -c $< -o $@

fuzz_xattrs: fuzz_xattrs.o stubs.o $(XATTRS_OBJS)
	$(CC) $(FUZZ_SAN) $^ -o $@ $(XATTRS_LIBS)

regression: all
	./run-regression.sh

clean:
	rm -f $(HARNESSES) *.o crash-* leak-* timeout-* oom-*
