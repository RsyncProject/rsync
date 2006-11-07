Summary: A program for synchronizing files over a network.
Name: rsync
Version: 2.6.9
Release: 1
Group: Applications/Internet
Source:	ftp://rsync.samba.org/pub/rsync/rsync-%{version}.tar.gz
URL: http://rsync.samba.org/

Prefix: %{_prefix}
BuildRoot: /var/tmp/%{name}-root
License: GPL

%description
Rsync uses a reliable algorithm to bring remote and host files into
sync very quickly. Rsync is fast because it just sends the differences
in the files over the network instead of sending the complete
files. Rsync is often used as a very powerful mirroring process or
just as a more capable replacement for the rcp command. A technical
report which describes the rsync algorithm is included in this
package.

%prep
%setup -q

%build
%configure

make

%install
rm -rf $RPM_BUILD_ROOT

%makeinstall

%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(-,root,root)
%doc COPYING README tech_report.tex
%{_prefix}/bin/rsync
%{_mandir}/man1/rsync.1*
%{_mandir}/man5/rsyncd.conf.5*

%changelog
* Thu Jan 30 2003 Horst von Brand <vonbrand@inf.utfsm.cl>
  Fixed "Sept" date in %changelog here
  Use %{_mandir} to point to manpages
  Support for compressed manpages (* at end catches them in %files)
  Add doc/README-SGML and doc/rsync.sgml to %doc

* Mon Sep 11 2000 John H Terpstra <jht@turbolinux.com>
  Changed target paths to be Linux Standards Base compliant

* Mon Jan 25 1999 Stefan Hornburg <racke@linuxia.de>
  quoted RPM_OPT_FLAGS for the sake of robustness

* Mon May 18 1998 Andrew Tridgell <tridge@samba.anu.edu.au>
  reworked for auto-building when I release rsync (tridge@samba.anu.edu.au)

* Sat May 16 1998 John H Terpstra <jht@aquasoft.com.au>
  Upgraded to Rsync 2.0.6
    -new feature anonymous rsync

* Mon Apr  6 1998 Douglas N. Arnold <dna@math.psu.edu>

Upgrade to rsync version 1.7.2.

* Sun Mar  1 1998 Douglas N. Arnold <dna@math.psu.edu>

Built 1.6.9-1 based on the 1.6.3-2 spec file of John A. Martin.
Changes from 1.6.3-2 packaging: added latex and dvips commands
to create tech_report.ps.

* Mon Aug 25 1997 John A. Martin <jam@jamux.com>

Built 1.6.3-2 after finding no rsync-1.6.3-1.src.rpm although there
was an ftp://ftp.redhat.com/pub/contrib/alpha/rsync-1.6.3-1.alpha.rpm
showing no packager nor signature but giving 
"Source RPM: rsync-1.6.3-1.src.rpm".

Changes from 1.6.2-1 packaging: added '$RPM_OPT_FLAGS' to make, strip
to '%build', removed '%prefix'.

* Thu Apr 10 1997 Michael De La Rue <miked@ed.ac.uk>

rsync-1.6.2-1 packaged.  (This entry by jam to credit Michael for the
previous package(s).)
