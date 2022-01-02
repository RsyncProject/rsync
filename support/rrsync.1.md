# NAME

rrsync - a script to setup restricted rsync users via ssh logins

# SYNOPSIS

```
rrsync [-ro|-rw] [-munge] [-no-del] DIR
```

# DESCRIPTION

A user's ssh login can be restricted to only allow the running of an rsync
transfer in one of two easy ways: forcing the running of the rrsync script
or forcing the running of an rsync daemon-over-ssh command.

To use the rrsync script, edit the user's `~/.ssh/authorized_keys` file and add
a prefix like one of the following (followed by a space) in front of each
ssh-key line that should be restricted:

> ```
> command="rrsync DIR"
> command="rrsync -ro DIR"
> command="rrsync -munge -no-del DIR"
> ```

Then, ensure that the rrsync script has your desired option restrictions. You
may want to copy the script to a local bin dir with a unique name if you want
to have multiple configurations. One or more rrsync options can be specified
prior to the `DIR` if you want to further restrict the transfer.

To use an rsync daemon setup, edit the user's `~/.ssh/authorized_keys` file and
add a prefix like one of the following (followed by a space) in front of each
ssh-key line that should be restricted:

> ```
> command="rsync --server --daemon ."
> command="rsync --server --daemon --config=/PATH/TO/rsyncd.conf ."
> ```

Then, ensure that the rsyncd.conf file is created with one or more module names
with the appropriate path and option restrictions.  If the `--config` option is
omitted, it defaults to `~/rsyncd.conf`.  See the `rsyncd.conf` man page for
details of how to configure an rsync daemon.

When using rrsync, there can be just one restricted dir per authorized key.  A
daemon setup, on the other hand, allows multiple module names inside the config
file, each one with its own path setting.

The remainder of this man page is dedicated to using the rrsync script.

# OPTION SUMMARY

```
-ro        Allow only reading from the DIR. Implies -no-del.
-wo        Allow only writing to the DIR.
-no-del    Disable rsync's --delete* and --remove* options.
-munge     Enable rsync's --munge-links on the server side.
-help, -h  Output this help message and exit.
```

A single non-option argument specifies the restricted DIR to use. It can be
relative to the user's home directory or an absolute path.

# SECURITY RESTRICTIONS

The rrsync script validates the path arguments it is sent to try to restrict
them to staying within the specified DIR.

The rrsync script rejects rsync's `--copy-links`` option (by default) so that a
copy cannot dereference a symlink within the DIR to get to a file outside the
DIR.

The rrsync script rejects rsync's `--protect-args` (`-s`) option because it
would allow options to be sent to the server-side that the script could not
check.  If you want to support `--protect-args`, use a daemon-over-ssh setup.

The rrsync script accepts just a subset of rsync's options that the real rsync
uses when running the server command.  A few extra convenience options are also
included to help it to interact with BackupPC and accept some convenient user
overrides.

The script (or a copy of it) can be manually edited if you want it to customize
the option handling.

# EXAMPLES

The `.ssh/authorized_keys` file might have lines in it like this:

> ```
> command="rrsync client/logs" ssh-rsa AAAAB3NzaC1yc2EAAAABIwAAAIEAzG...
> command="rrsync -ro results" ssh-rsa AAAAB3NzaC1yc2EAAAABIwAAAIEAmk...
> ```
