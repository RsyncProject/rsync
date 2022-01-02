#!/usr/bin/env python3
# This script lets you update a hierarchy of files in an atomic way by
# first creating a new hierarchy using rsync's --link-dest option, and
# then swapping the hierarchy into place.  **See the usage message for
# more details and some important caveats!**

import os, sys, re, subprocess, shutil

ALT_DEST_ARG_RE = re.compile('^--[a-z][^ =]+-dest(=|$)')

RSYNC_PROG = '/usr/bin/rsync'

def main():
    cmd_args = sys.argv[1:]
    if '--help' in cmd_args:
        usage_and_exit()

    if len(cmd_args) < 2:
        usage_and_exit(True)

    dest_dir = cmd_args[-1].rstrip('/')
    if dest_dir == '' or dest_dir.startswith('-'):
        usage_and_exit(True)

    if not os.path.isdir(dest_dir):
        die(dest_dir, "is not a directory or a symlink to a dir.\nUse --help for help.")

    bad_args = [ arg for arg in cmd_args if ALT_DEST_ARG_RE.match(arg) ]
    if bad_args:
        die("You cannot use the", ' or '.join(bad_args), "option with atomic-rsync.\nUse --help for help.")

    # We ignore exit-code 24 (file vanished) by default.
    allowed_exit_codes = '0 ' + os.environ.get('ATOMIC_RSYNC_OK_CODES', '24')
    try:
        allowed_exit_codes = set(int(num) for num in re.split(r'[, ]+', allowed_exit_codes) if num != '')
    except ValueError:
        die('Invalid integer in ATOMIC_RSYNC_OK_CODES:', allowed_exit_codes[2:])

    symlink_content = os.readlink(dest_dir) if os.path.islink(dest_dir) else None

    dest_arg = dest_dir
    dest_dir = os.path.realpath(dest_dir) # The real destination dir with all symlinks dereferenced
    if dest_dir == '/':
        die('You must not use "/" as the destination directory.\nUse --help for help.')

    old_dir = new_dir = None
    if symlink_content is not None and dest_dir.endswith(('-1','-2')):
        if not symlink_content.endswith(dest_dir[-2:]):
            die("Symlink suffix out of sync with dest_dir name:", symlink_content, 'vs', dest_dir)
        num = 3 - int(dest_dir[-1]);
        old_dir = None
        new_dir = dest_dir[:-1] + str(num)
        symlink_content = symlink_content[:-1] + str(num)
    else:
        old_dir = dest_dir + '~old~'
        new_dir = dest_dir + '~new~'

    cmd_args[-1] = new_dir + '/'

    if old_dir is not None and os.path.isdir(old_dir):
        shutil.rmtree(old_dir)
    if os.path.isdir(new_dir):
        shutil.rmtree(new_dir)

    child = subprocess.run([RSYNC_PROG, '--link-dest=' + dest_dir, *cmd_args])
    if child.returncode not in allowed_exit_codes:
        die('The rsync copy failed with code', child.returncode, exitcode=child.returncode)

    if not os.path.isdir(new_dir):
        die('The rsync copy failed to create:', new_dir)

    if old_dir is None:
        atomic_symlink(symlink_content, dest_arg)
    else:
        os.rename(dest_dir, old_dir)
        os.rename(new_dir, dest_dir)


def atomic_symlink(target, link):
    newlink = link + "~new~"
    try:
        os.unlink(newlink); # Just in case
    except OSError:
        pass
    os.symlink(target, newlink)
    os.rename(newlink, link)


def usage_and_exit(use_stderr=False):
    usage_msg = """\
Usage: atomic-rsync [RSYNC-OPTIONS] [HOST:]/SOURCE/DIR/ /DEST/DIR/
       atomic-rsync [RSYNC-OPTIONS] HOST::MOD/DIR/ /DEST/DIR/

This script lets you update a hierarchy of files in an atomic way by first
creating a new hierarchy (using hard-links to leverage the existing files),
and then swapping the new hierarchy into place.  You must be pulling files
to a local directory, and that directory must already exist.  For example:

    mkdir /local/files-1
    ln -s files-1 /local/files
    atomic-rsync -aiv host:/remote/files/ /local/files/

If /local/files is a symlink to a directory that ends in -1 or -2, the copy
will go to the alternate suffix and the symlink will be changed to point to
the new dir.  This is a fully atomic update.  If the destination is not a
symlink (or not a symlink to a *-1 or a *-2 directory), this will instead
create a directory with "~new~" suffixed, move the current directory to a
name with "~old~" suffixed, and then move the ~new~ directory to the original
destination name (this double rename is not fully atomic, but is rapid).  In
both cases, the prior destintaion directory will be preserved until the next
update, at which point it will be deleted.

By default, rsync exit-code 24 (file vanished) is allowed without halting the
atomic update.  If you want to change that, specify the environment variable
ATOMIC_RSYNC_OK_CODES with numeric values separated by spaces and/or commas.
Specify an empty string to only allow a successful copy.  An override example:

    ATOMIC_RSYNC_OK_CODES='23 24' atomic-rsync -aiv host:src/ dest/

See the errcode.h file for a list of all the exit codes.

See the "rsync" command for its list of options.  You may not use the
--link-dest, --compare-dest, or --copy-dest options (since this script
uses --link-dest to make the transfer efficient).
"""
    print(usage_msg, file=sys.stderr if use_stderr else sys.stdout)
    sys.exit(1 if use_stderr else 0)


def die(*args, exitcode=1):
    print(*args, file=sys.stderr)
    sys.exit(exitcode)


if __name__ == '__main__':
    main()

# vim: sw=4 et
