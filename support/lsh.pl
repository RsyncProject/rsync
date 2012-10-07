#!/usr/bin/perl
# This script can be used as a "remote shell" command that is only
# capable of pretending to connect to "localhost".  This is useful
# for testing or for running a local copy where the sender and the
# receiver needs to use different options (e.g. --fake-super).  If
# we get -l USER, we try to become the USER, either directly (must
# be root) or by using "sudo -H -u USER" (requires --sudo option).

use strict;
use warnings;
use Getopt::Long;
use English '-no_match_vars';

&Getopt::Long::Configure('bundling');
&Getopt::Long::Configure('require_order');
GetOptions(
    'l=s' => \( my $login_name ),
    '1|2|4|6|A|a|C|f|g|k|M|N|n|q|s|T|t|V|v|X|x|Y' => sub { }, # Ignore
    'b|c|D|e|F|i|L|m|O|o|p|R|S|w=s' => sub { }, # Ignore
    'no-cd' => \( my $no_chdir ),
    'sudo' => \( my $use_sudo ),
) or &usage;
&usage unless @ARGV > 1;

my $host = shift;
if ($host =~ s/^([^@]+)\@//) {
    $login_name = $1;
}
if ($host ne 'localhost') {
    die "lsh: unable to connect to host $host\n";
}

my ($home_dir, @cmd);
if ($login_name) {
    my ($uid, $gid);
    if ($login_name =~ /\D/) {
	$uid = getpwnam($login_name);
	die "Unknown user: $login_name\n" unless defined $uid;
    } else {
	$uid = $login_name;
    }
    ($login_name, $gid, $home_dir) = (getpwuid($uid))[0,3,7];
    if ($use_sudo) {
	unshift @ARGV, "cd '$home_dir' &&" unless $no_chdir;
	unshift @cmd, qw( sudo -H -u ), $login_name;
	$no_chdir = 1;
    } else {
	my $groups = "$gid $gid";
	while (my ($grgid, $grmembers) = (getgrent)[2,3]) {
	    if ($grgid != $gid && $grmembers =~ /(^|\s)\Q$login_name\E(\s|$)/o) {
		$groups .= " $grgid";
	    }
	}

	my ($ruid, $euid) = ($UID, $EUID);
	$GID = $EGID = $groups;
	$UID = $EUID = $uid;
	die "Cannot set ruid: $! (use --sudo?)\n" if $UID == $ruid && $ruid != $uid;
	die "Cannot set euid: $! (use --sudo?)\n" if $EUID == $euid && $euid != $uid;

	$ENV{USER} = $ENV{USERNAME} = $login_name;
	$ENV{HOME} = $home_dir;
    }
} else {
    $home_dir = (getpwuid($UID))[7];
}

unless ($no_chdir) {
    chdir $home_dir or die "Unable to chdir to $home_dir: $!\n";
}

push @cmd, '/bin/sh', '-c', "@ARGV";
exec @cmd;
die "Failed to exec: $!\n";

sub usage
{
    die <<EOT;
Usage: lsh [-l user] [--sudo] [--no-cd] localhost COMMAND [...]
EOT
}
