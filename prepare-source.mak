gen: conf proto.h man

conf: configure.sh config.h.in

configure.sh: configure.in aclocal.m4
	autoconf -o configure.sh

config.h.in: configure.in aclocal.m4
	autoheader && touch config.h.in

proto.h: *.c lib/compat.c
	perl mkproto.pl *.c lib/compat.c

man: rsync.1 rsyncd.conf.5

rsync.1: rsync.yo
	yodl2man -o rsync.1 rsync.yo
	-./tweak_manpage rsync.1

rsyncd.conf.5: rsyncd.conf.yo
	yodl2man -o rsyncd.conf.5 rsyncd.conf.yo
	-./tweak_manpage rsyncd.conf.5
