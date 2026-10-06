#!/bin/sh
# Regenerate the autoconf build files from configure.ac.
# Run this after "make distclean" or a fresh clone before ./configure.
set -e
aclocal -I m4
autoconf -o configure.sh
autoheader && touch config.h.in
