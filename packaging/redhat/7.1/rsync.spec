Summary: Program for efficient remote updates of files.
Name: rsync
Version: 2.5.7
Release: 1
Copyright: GPL
Group: Applications/Networking
Source:	ftp://samba.anu.edu.au/pub/rsync/rsync-2.5.7.tar.gz
URL: http://samba.anu.edu.au/rsync/
Packager: Andrew Tridgell <tridge@samba.anu.edu.au>
BuildRoot: /tmp/rsync

%description
rsync is a replacement for rcp that has many more features.

rsync uses the "rsync algorithm" which provides a very fast method for
bringing remote files into sync. It does this by sending just the
differences in the files across the link, without requiring that both
sets of files are present at one of the ends of the link beforehand.

A technical report describing the rsync algorithm is included with
this package. 

%changelog
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

%prep
%setup

%build
./configure --prefix=/usr
make CFLAGS="$RPM_OPT_FLAGS"
strip rsync

%install
mkdir -p $RPM_BUILD_ROOT/usr/{bin,man/{man1,man5}}
install -m755 rsync $RPM_BUILD_ROOT/usr/bin
install -m644 rsync.1* $RPM_BUILD_ROOT/usr/man/man1
install -m644 rsyncd.conf.5* $RPM_BUILD_ROOT/usr/man/man5

%clean
rm -rf $RPM_BUILD_ROOT

%files
%attr(-,root,root) /usr/bin/rsync
%attr(-,root,root) /usr/man/man1/rsync.1*
%attr(-,root,root) /usr/man/man5/rsyncd.conf.5*
%attr(-,root,root) %doc tech_report.tex
%attr(-,root,root) %doc README
%attr(-,root,root) %doc COPYING
