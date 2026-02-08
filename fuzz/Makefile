# Makefile for rsync fuzz targets.
#
# Usage:
#   # First build rsync normally from the project root:
#   ./configure --disable-md2man --disable-xxhash --disable-zstd --disable-lz4 \
#       --with-included-popt CC=clang CFLAGS="-g -O1 -fsanitize=fuzzer-no-link,address"
#   make
#
#   # Then build the fuzz targets:
#   make -C fuzz
#
# To run a fuzzer:
#   ./fuzz/fuzz_wildmatch fuzz/corpus_wildmatch/
#
# For OSS-Fuzz integration, see projects/rsync/ in the google/oss-fuzz repo.

CC ?= clang
CFLAGS ?= -g -O1 -fsanitize=fuzzer-no-link,address
FUZZ_ENGINE ?= -fsanitize=fuzzer

# rsync source root (parent directory)
RSYNC_DIR = ..

# Object files from the rsync build (excluding main.o and cleanup.o which
# conflict with our stubs)
RSYNC_OBJS = $(shell find $(RSYNC_DIR) -maxdepth 2 -name '*.o' \
    ! -name 'main.o' ! -name 'cleanup.o' ! -name 'wildtest.o' \
    ! -name 'trimslash.o' ! -name 't_unsafe.o' ! -name 'testrun.o' \
    ! -name 'tls.o' ! -name 'getgroups.o' ! -name 'getfsdev.o' \
    ! -name 't_stub.o' ! -name 'fuzz_*.o' ! -path '*/fuzz/*' \
    2>/dev/null)

LIBCRYPTO = $(shell find /usr/lib* -name 'libcrypto.a' -print -quit 2>/dev/null)
ifeq ($(LIBCRYPTO),)
    LIBCRYPTO = -lcrypto
endif

TARGETS = fuzz_wildmatch fuzz_rsyncd_conf fuzz_parse_filter

all: $(TARGETS)

fuzz_wildmatch: fuzz_wildmatch.c $(RSYNC_OBJS)
	$(CC) $(CFLAGS) -I$(RSYNC_DIR) -c fuzz_wildmatch.c -o fuzz_wildmatch.o
	$(CXX) $(CFLAGS) $(FUZZ_ENGINE) fuzz_wildmatch.o $(RSYNC_OBJS) $(LIBCRYPTO) -lstdc++ -o $@

fuzz_rsyncd_conf: fuzz_rsyncd_conf.c $(RSYNC_OBJS)
	$(CC) $(CFLAGS) -I$(RSYNC_DIR) -c fuzz_rsyncd_conf.c -o fuzz_rsyncd_conf.o
	$(CXX) $(CFLAGS) $(FUZZ_ENGINE) fuzz_rsyncd_conf.o $(RSYNC_OBJS) $(LIBCRYPTO) -lstdc++ -o $@

fuzz_parse_filter: fuzz_parse_filter.c $(RSYNC_OBJS)
	$(CC) $(CFLAGS) -I$(RSYNC_DIR) -c fuzz_parse_filter.c -o fuzz_parse_filter.o
	$(CXX) $(CFLAGS) $(FUZZ_ENGINE) fuzz_parse_filter.o $(RSYNC_OBJS) $(LIBCRYPTO) -lstdc++ -o $@

clean:
	rm -f $(TARGETS) fuzz_*.o

.PHONY: all clean
