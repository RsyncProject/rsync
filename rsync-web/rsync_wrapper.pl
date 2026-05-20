#      __
#     /\ \ From the mind of
#    /  \ \
#   / /\ \ \_____ Lee Eakin  <LEakin@Nostrum.COM>
#  /  \ \ \______\       or  <Lee@Eakin.ORG>
# / /\ \ \/____  /
# \ \ \ \____\/ / Wrapper module for the rsync program
#  \ \ \/____  /  rsync can be found at http://rsync.samba.org/rsync/
#   \ \____\/ /
#    \/______/

package Rsync;
require 5.004;

use FileHandle;
use IPC::Open3 qw(open3);
use Carp;

use strict;

=head1 NAME

Rsync - perl module interface to B<rsync> http://rsync.samba.org/rsync/

=head1 SYNOPSIS

use Rsync;

$obj = Rsync->new(qw(-az C<-e> /usr/local/bin/ssh
         --rsync-path /usr/local/bin/rsync));

$obj->exec(qw(localdir rhost:remdir))
         or warn "rsync failed\n";

=head1 DESCRIPTION

Perl Convenience wrapper for B<rsync> program.  Written for B<rsync> 2.3.1 but
should perform properly with most versions.

=cut

# options from the rsync man pae
###### Boolean flags ######
# -h, --help                  show this help screen
# -v, --verbose               increase verbosity
# -q, --quiet                 decrease verbosity
# -c, --checksum              always checksum
# -a, --archive               archive mode
# -r, --recursive             recurse into directories
# -R, --relative              use relative path names
# -b, --backup                make backups (default ~  suffix)
# -u, --update                update only (don't overwrite newer files)
# -l, --links                 preserve soft links
# -L, --copy-links            treat soft links like regular files
#     --copy-unsafe-links     copy links outside the source tree
#     --safe-links            ignore links outside the destination tree
# -H, --hard-links            preserve hard links
# -p, --perms                 preserve permissions
# -o, --owner                 preserve owner (root only)
# -g, --group                 preserve group
# -D, --devices               preserve devices (root only)
# -t, --times                 preserve times
# -S, --sparse                handle sparse files efficiently
# -n, --dry-run               show what would have been transferred
# -W, --whole-file            copy whole files, no incremental checks
# -x, --one-file-system       don't cross filesystem boundaries
# -C, --cvs-exclude           auto ignore files in the same way CVS does
#                             RCS SCCS CVS CVS.adm RCSLOG cvslog.* tags TAGS
#                             .make.state .nse_depinfo *~ #* .#* ,* *.old *.bak
#                             *.BAK *.orig *.rej .del-* *.a *.o *.obj *.so *.Z
#                             *.elc *.ln core
#     --delete                delete files that don't exist on the sending side
#     --delete-excluded       also delete excluded files on the receiving side
#     --partial               keep partially transferred files
#     --force                 force deletion of directories even if not empty
#     --numeric-ids           don't map uid/gid values by user/group name
# -I, --ignore-times          don't exclude files that match length and time
#     --size-only             only use file size when determining if a file
#                             should be transferred
# -z, --compress              compress file data
#     --version               print version number
#     --daemon                run as a rsync daemon
#     --stats                 give some file transfer stats
#     --progress              show progress during transfer
###### scalar values ######
#     --csum-length=LENGTH    <=16 bit md4 checksum size
# -B, --block-size=SIZE       checksum blocking size (default 700)
#     --timeout=TIME          set IO timeout in seconds
#     --port=PORT             specify alternate rsyncd port number
# -e, --rsh=COMMAND           specify rsh replacement
# -T, --temp-dir=DIR          create temporary files in directory DIR
#     --compare-dest=DIR      also compare destination files relative to DIR
#     --exclude-from=FILE     exclude patterns listed in FILE
#     --include-from=FILE     don't exclude patterns listed in FILE
#     --config=FILE           specify alternate rsyncd.conf file
#     --password-file=FILE    get password from FILE
#     --log-format=FORMAT     log file transfers using specified format
#     --suffix=SUFFIX         override backup suffix
#     --rsync-path=PATH       specify path to rsync on the remote machine
###### array values ######
#     --exclude=PATTERN       exclude files matching PATTERN
#     --include=PATTERN       don't exclude files matching PATTERN
#

=over 4

=item Rsync::new

$obj = new Rsync;

   or

$obj = Rsync->new;

   or

$obj = Rsync->new(@options);

=back

Create an Rsync object.  Any options passed at creation are stored in
the object as defaults for all future exec call on that object.  Options
are the same as those in L<rsync> with the addition of
--path-to-rsync which can be used to override the hardcoded default of
/usr/local/bin/rsync, and --debug which causes the module functions to
print some debugging information to STDERR.

=cut

sub new {
   my $class=shift;

   # seed the options hash, booleans, scalars, excludes, data,
   # status, stderr/stdout storage for last exec
   my $self={
      # the full path name to the rsync binary
      'path-to-rsync' => '/usr/local/bin/rsync',
      # these are the boolean flags to rsync, all default off, including them
      # in the args list turns them on
      'flag' => {qw(
         --archive         0   --backup          0   --checksum          0
         --compress        0   --copy-links      0   --copy-unsafe-links 0
         --cvs-exclude     0   --daemon          0   --delete            0
         --delete-excluded 0   --devices         0   --dry-run           0
         --force           0   --group           0   --hard-links        0
         --help            0   --ignore-times    0   --links             0
         --numeric-ids     0   --one-file-system 0   --owner             0
         --partial         0   --perms           0   --progress          0
         --quiet           0   --recursive       0   --relative          0
         --safe-links      0   --size-only       0   --sparse            0
         --stats           0   --times           0   --update            0
         --verbose         0   --version         0   --whole-file        0
      )},
      # these have simple scalar args we cannot easily check
      'scalar' => {qw(
         --block-size      0   --compare-dest    0   --config            0
         --csum-length     0   --exclude-from    0   --include-from      0
         --log-format      0   --password-file   0   --port              0
         --rsh             0   --rsync-path      0   --suffix            0
         --temp-dir        0   --timeout         0
      )},
      # these can be specified multiple times and are additive, the doc also
      # specifies that it is an ordered list so we must preserve that order
      'exclude'     => [],
      # source/destination path names and hostnames
      'data'        => [],
      # return status from last exec
      'status'      => 0,
      'realstatus'  => 0,
      # whether or not to print debug statements
      'debug'       => 0,
      # stderr from last exec in array format (messages from remote rsync proc)
      'err'         => [],
      # stdout from last exec in array format (messages from local rsync proc)
      'out'         => [],
   };
   if (@_) {
      &defopts($self,@_) or return undef;
   }
   bless $self, $class;
}

=over 4

=item Rsync::defopts

defopts $obj @options;

   or

$obj->defopts(@options);

=back

Set default options for future exec calls for the object.  See L<rsync>
for a complete list of valid options.  This is really the internal
function that B<new> calls but you can use it too.  Presently there is no way
to turn off the boolean options short of creating another object, but if it is
needed and the B<rsync> guys don't use it, I may add hooks to let + and ++ or a
leading no- toggle it back off similar to B<Getopt::Long> (the GNU way).

=cut

sub defopts {
   my $self=shift;
   my @opts=@_;

   # need a conversion table in case someone uses the short options
   my %short=(qw(
      -B  --block-size   -C  --cvs-exclude     -D  --devices   -H  --hard-links
      -I  --ignore-times -L  --copy-links      -R  --relative  -T  --temp-dir
      -W  --whole-file   -a  --archive         -b  --backup    -c  --checksum
      -e  --rsh          -g  --group           -h  --help      -l  --links
      -n  --dry-run      -o  --owner           -p  --perms     -q  --quiet
      -r  --recursive    -s  --sparse          -t  --times     -u  --update
      -v  --verbose      -x  --one-file-system -z  --compress
   ));
   while (my $opt=shift @opts) {
      my $arg;
      print(STDERR "setting debug flag\n"),$self->{debug}=1,next
         if $opt eq '--debug';
      print STDERR "processing option: $opt\n" if $self->{debug};
      if ($opt=~/^-/) {
         # handle short opts first
         if ($opt=~/^-(\w+)$/) {
            foreach (split '',$1) {
               print STDERR "short option: -$_\n" if $self->{debug};
               if (exists $short{'-'.$_}) {
                  $opt=$short{'-'.$_};
                  # convert it to the long form
                  $opt=$short{$opt} if exists $short{$opt};
                  # handle the 3 short opts that require args
                  $self->{scalar}{$opt}=shift(@opts),next if (/^[BeT]$/);
                  # handle the rest
                  $self->{flag}{$opt}=1,next if exists $self->{flag}{$opt};
               }
               carp "$opt - unknown option\n";
               return undef;
            }
         }
         # handle long opts with = args
         if ($opt=~/^(--\w+[\w-]*)=(.*)$/) {
            print STDERR "splitting longopt: $opt ($1 $2)\n" if $self->{debug};
            ($opt,$arg)=($1,$2);
         }
         # handle boolean flags
         $self->{flag}{$opt}=1,next if exists $self->{flag}{$opt};
         # handle simple scalars
         $self->{scalar}{$opt}=($arg || shift @opts),next
            if exists $self->{scalar}{$opt};
         # handle excludes
         if ($opt eq '--exclude') {
            $arg||=shift @opts;
            # if they sent a reset, we will too
            $self->{exclude}=[],next if $arg eq '!';
            # otherwise add it to the list
            push @{$self->{exclude}},$arg;
            next;
         }
         # to preserve order we store both includes and excludes in the same
         # array.  We use the leading '+ ' (plus space) trick from the man
         # page to accomplish this.
         if ($opt eq '--include') {
            $arg||=shift @opts;
            # first check to see if this is really an exclude
            push(@{$self->{exclude}},$arg),next if $arg=~s/^- //;
            # next see if they sent a reset, if they did, we will too
            $self->{exclude}=[],next if $arg eq '!';
            # if it really is an include, fix it first, since we use exclude
            $arg='+ '.$arg unless $arg=~/^\+ /;
            push @{$self->{exclude}},$arg;
            next;
         }
         # handle our special case to override hard-coded path to rsync
         $self->{'path-to-rsync'}=($arg || shift @opts),next
            if $opt eq '--path-to-rsync';
         # if we get this far nothing matched so it must be an error
         carp "$opt - unknown option\n";
         return undef;
      } else { # must be data (source/destination info)
         print STDERR "adding to data array: $opt\n" if $self->{debug};
         push(@{$self->{data}},$opt);
      }
   }
   1;
}

=over 4

=item Rsunc::exec

exec $obj @options or warn "rsync failed\n";

   or

$obj->exec(@options) or warn "rsync failed\n";

=back

This is the function that does the real work.  Any options passed to this
routine are appended to any pre-set options and are not saved.  They effect
the current execution of B<rsync> only.  It returns 1 if the return status was
zero (or true), if the B<rsync> return status was non-zero it returns undef and
stores the return status.  You can examine the return status from rsync and
any output to stdout and stderr with the functions listed below.

=cut

sub exec {
   my $self=shift;
   my @cmd=($self->{'path-to-rsync'});

   foreach (sort keys %{$self->{flag}}) {
      push @cmd,$_ if $self->{flag}{$_};
   }
   foreach (sort keys %{$self->{scalar}}) {
      push @cmd,$_.'='.$self->{scalar}{$_} if $self->{scalar}{$_};
   }
   foreach (@{$self->{exclude}}) {
      push @cmd,'--exclude='.$_;
   }
   foreach (@{$self->{data}}) {
      push @cmd,$_;
   }
   push @cmd,@_ if @_;
   print STDERR "exec: @cmd\n" if $self->{debug};
   my $in=FileHandle->new; my $out=FileHandle->new; my $err=FileHandle->new;
   my $pid=eval{ open3 $in,$out,$err,@cmd };
   if ($@) {
      $self->{realstatus}=0;
      $self->{status}=255;
      $self->{err}=[$@,"Execution of rsync failed.\n"];
      return undef;
   }
   $in->close; # we don't use it and neither should rsync (at least not yet)
   $self->{err}=[ $err->getlines ];
   $self->{out}=[ $out->getlines ];
   $err->close;
   $out->close;
   waitpid $pid,0;
   $self->{realstatus}=$?;
   $self->{status}=$?>>8;
   return undef if $self->{status};
   return 1;
}

=over 4

=item Rsync::status

$rval = status $obj;

   or

$rval = $obj->status;

=back

Returns the status from last B<exec> call right shifted 8 bits.

=cut

sub status {
   my $self=shift;
   return $self->{status};
}

=over 4

=item Rsync::realstatus

$rval = realstatus $obj;

   or

$rval = $obj->realstatus;

=back

Returns the real status from last B<exec> call (not right shifted).

=cut

sub realstatus {
   my $self=shift;
   return $self->{realstatus};
}

=over 4

=item Rsync::err

$aref = err $obj;

   or

$aref = $obj->err;

=back

Returns an array or a reference to the array containing all output to stderr
from the last B<exec> call.  B<rsync> sends all messages from the remote
B<rsync> process to stderr.  This functions purpose is to make it easier for
you to parse that output for appropriate information.

=cut

sub err {
   my $self=shift;
   return(wantarray ? @{$self->{err}} : $self->{err});
}

=over 4

=item Rsync::out

$aref = out $obj;

   or

$aref = $obj->out;

=back

Similar to the B<err> function, this returns an array or a reference to the
array containing all output to stdout from the last B<exec> call.  B<rsync>
sends all messages from the local B<rsync> process to stdout.

=cut

sub out {
   my $self=shift;
   return(wantarray ? @{$self->{out}} : $self->{out});
}

=head1 Author

Lee Eakin E<lt>leakin@nostrum.comE<gt>

=head1 Credits

Gerard Hickey                             C<PGP::Pipe>

Russ Allbery                              C<PGP::Sign>

Graham Barr                               C<Net::*>

Andrew Tridgell and Paul Mackerras        C<rsync(1)>

John Steele   E<lt>steele@nostrum.comE<gt>

Philip Kizer  E<lt>pckizer@nostrum.comE<gt>

Larry Wall                                C<perl(1)>

I borrowed many clues on wrapping an external program from the PGP modules,
and I would not have had such a useful tool to wrap except for the great work
of the B<rsync> authors.  Thanks also to Graham Barr, the author of the libnet
modules and many others, for looking over this code.  Of course I must mention
the other half of my brain, John Steele, and his good friend Philip Kizer for
finding B<rsync> and bringing it to my attention.  And I would not have been
able to enjoy writing useful tools if not for the creator of the B<perl>
language.

=head1 Copyrights

      Copyleft (l) 1999, by Lee Eakin

=cut

1;
