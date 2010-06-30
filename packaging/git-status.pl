# Do some git-status checking for the current dir and (optionally)
# the patches dir.

sub check_git_state
{
    my($master_branch, $fatal_unless_clean, $check_patches_dir) = @_;

    my($cur_branch) = check_git_status($fatal_unless_clean);
    if ($cur_branch ne $master_branch) {
	print "The checkout is not on the $master_branch branch.\n";
	exit 1 if $master_branch ne 'master';
	print "Do you want me to continue with --branch=$cur_branch? [n] ";
	$_ = <STDIN>;
	exit 1 unless /^y/i;
	$_[0] = $master_branch = $cur_branch; # Updates caller's $master_branch too.
    }

    if ($check_patches_dir && -d 'patches/.git') {
	($cur_branch) = check_git_status($fatal_unless_clean, 'patches');
	if ($cur_branch ne $master_branch) {
	    print "The *patches* checkout is on branch $cur_branch, not branch $master_branch.\n";
	    print "Do you want to change it to branch $master_branch? [n] ";
	    $_ = <STDIN>;
	    exit 1 unless /^y/i;
	    system "cd patches && git checkout '$master_branch'";
	}
    }
}

sub check_git_status
{
    my($fatal_unless_clean, $subdir) = @_;
    $subdir = '.' unless defined $subdir;
    my $status = `cd '$subdir' && git status`;
    my $is_clean = $status =~ /\nnothing to commit \(working directory clean\)/;
    my($cur_branch) = $status =~ /^# On branch (.+)\n/;
    if ($fatal_unless_clean && !$is_clean) {
	if ($subdir eq '.') {
	    $subdir = '';
	} else {
	    $subdir = " *$subdir*";
	}
	die "The$subdir checkout is not clean:\n", $status;
    }
    ($cur_branch, $is_clean, $status);
}

1;
