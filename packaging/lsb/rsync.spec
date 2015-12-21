Summary: A fast, versatile, remote (and local) file-copying tool
Name: rsync
Version: 3.1.2
%define fullversion %{version}
Release: 1
%define srcdir src
Group: Applications/Internet
License: GPL
Source0: http://rsync.samba.org/ftp/rsync/%{srcdir}/rsync-%{fullversion}.tar.gz
#Source1: http://rsync.samba.org/ftp/rsync/%{srcdir}/rsync-patches-%{fullversion}.tar.gz
URL: http://rsync.samba.org/

Prefix: %{_prefix}
BuildRoot: /var/tmp/%{name}-root

%package ssl-client
Summary: Provides rsync-ssl
Group: Applications/Internet
Requires: rsync, stunnel >= 4

%package ssl-daemon
Summary: An stunnel config file to support ssl rsync daemon connections.
Group: Applications/Internet
Requires: rsync, stunnel >= 4

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

%description ssl-client
Provides the rsync-ssl script that makes use of stunnel 4 to open an ssl
connection to an rsync daemon (on port 874).  This setup does NOT require
any local stunnel daemon to be running to connect to the remote ssl rsyncd.

%description ssl-daemon
Provides a config file for stunnel that will (if you start your stunnel
service) cause stunnel to listen for ssl rsync-daemon connections and run
"rsync --daemon" to handle them.

%prep
# Choose one -- setup source only, or setup source + rsync-patches:
%setup -q -n rsync-%{fullversion}
#%setup -q -b1 -n rsync-%{fullversion}

# If you you used "%setup -q -b1 ...", choose the patches you wish to apply:
#patch -p1 <patches/acls.diff
#patch -p1 <patches/xattrs.diff
#patch -p1 <patches/remote-option.diff
#patch -p1 <patches/db.diff

# Avoid extra perl dependencies for scripts going into doc dir.
chmod -x support/*

%build
#./prepare-source
%configure

make

%install
rm -rf $RPM_BUILD_ROOT
make install install-ssl-client install-ssl-daemon DESTDIR=$RPM_BUILD_ROOT

mkdir -p $RPM_BUILD_ROOT/etc/xinetd.d $RPM_BUILD_ROOT/etc/rsync-ssl/certs
install -m 644 packaging/lsb/rsync.xinetd $RPM_BUILD_ROOT/etc/xinetd.d/rsync

%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(-,root,root)
%doc COPYING NEWS OLDNEWS README support/ tech_report.tex
%config(noreplace) /etc/xinetd.d/rsync
%{_prefix}/bin/rsync
%{_mandir}/man1/rsync.1*
%{_mandir}/man5/rsyncd.conf.5*

%files ssl-client
%{_prefix}/bin/rsync-ssl
%{_prefix}/bin/stunnel-rsync

%files ssl-daemon
%config(noreplace) /etc/stunnel/rsyncd.conf
%dir /etc/rsync-ssl/certs

%changelog
* Mon Dec 21 2015 Wayne Davison <wayned@samba.org>
Released 3.1.2.

* Fri Mar 21 2008 Wayne Davison <wayned@samba.org>
Added installation of /etc/xinetd.d/rsync file and some commented-out
lines that demonstrate how to use the rsync-patches tar file.
