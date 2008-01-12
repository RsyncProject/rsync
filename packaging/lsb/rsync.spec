Summary: A fast, versatile, remote (and local) file-copying tool
Name: rsync
Version: 3.0.0pre8
Release: 1
Group: Applications/Internet
Source:	http://rsync.samba.org/ftp/rsync/rsync-%{version}.tar.gz
URL: http://rsync.samba.org/

Prefix: %{_prefix}
BuildRoot: /var/tmp/%{name}-root
License: GPL

%description
Rsync is a fast and extraordinarily versatile file copying tool.  It can
copy locally, to/from another host over any remote shell, or to/from a
remote rsync daemon.  It offers a large number of options that control
every aspect of its behavior and permit very flexible specification of the
set of files to be copied.  It is famous for its delta-transfer algorithm,
which reduces the amount of data sent over the network by sending only the
differences between the source files and the existing files in the
destination.  Rsync is widely used for backups and mirroring and as an
improved copy command for everyday use.

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
* Sat Jan 12 2008 Wayne Davison <wayned@samba.org>
Released 3.0.0pre8.
