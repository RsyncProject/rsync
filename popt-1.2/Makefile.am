# Makefile for popt library.

AUTOMAKE_OPTIONS = 1.3 foreign

SUBDIRS = po

LDFLAGS = -L$(top_builddir)
INCLUDES = -I$(top_srcdir)

noinst_PROGRAMS = test1
test1_SOURCES = test1.c
test1_LDADD = -lpopt

include_HEADERS = popt.h
lib_LIBRARIES = libpopt.a
libpopt_a_SOURCES = popt.c findme.c poptparse.c poptconfig.c popthelp.c

CVSTAG = $(PACKAGE)-$(subst .,_,$(VERSION))

.PHONY: archive
archive: 
	@echo "This is $(PACKAGE)-$(VERSION)."
	@sleep 5
	@cvs -Q tag -F $(CVSTAG) .
	@rm -rf /tmp/$(PACKAGE)-$(VERSION) /tmp/$(PACKAGE)
	@cd /tmp; cvs -Q -d $(CVSROOT) export -r$(CVSTAG) $(PACKAGE) || :
	@mv /tmp/$(PACKAGE) /tmp/$(PACKAGE)-$(VERSION)
	@cd /tmp/$(PACKAGE)-$(VERSION); ./autogen.sh ; make depend; make distclean
	@cd /tmp/$(PACKAGE)-$(VERSION); ./autogen.sh --noconfigure
	@cd /tmp; tar czSpf $(PACKAGE)-$(VERSION).tar.gz $(PACKAGE)-$(VERSION)
	@rm -rf /tmp/$(PACKAGE)-$(VERSION)
	@cp /tmp/$(PACKAGE)-$(VERSION).tar.gz .
	@rm -f /tmp/$(PACKAGE)-$(VERSION).tar.gz 
	@echo " "
	@echo "The final archive is ./$(PACKAGE)-$(VERSION).tar.gz."
