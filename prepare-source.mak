conf: configure.sh config.h.in

configure.sh: configure.in aclocal.m4
	autoconf -o configure.sh

config.h.in: configure.in aclocal.m4
	autoheader && touch config.h.in
