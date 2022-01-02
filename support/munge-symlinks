#!/usr/bin/env python3
# This script will either prefix all symlink values with the string
# "/rsyncd-munged/" or remove that prefix.

import os, sys, argparse

SYMLINK_PREFIX = '/rsyncd-munged/'
PREFIX_LEN = len(SYMLINK_PREFIX)

def main():
    for arg in args.names:
        if os.path.islink(arg):
            process_one_arg(arg)
        elif os.path.isdir(arg):
            for fn in find_symlinks(arg):
                process_one_arg(fn)
        else:
            print("Arg is not a symlink or a dir:", arg, file=sys.stderr)


def find_symlinks(path):
    for entry in os.scandir(path):
        if entry.is_symlink():
            yield entry.path
        elif entry.is_dir(follow_symlinks=False):
            yield from find_symlinks(entry.path)


def process_one_arg(fn):
    lnk = os.readlink(fn)
    if args.unmunge:
        if not lnk.startswith(SYMLINK_PREFIX):
            return
        lnk = lnk[PREFIX_LEN:]
        while args.all and lnk.startswith(SYMLINK_PREFIX):
            lnk = lnk[PREFIX_LEN:]
    else:
        if not args.all and lnk.startswith(SYMLINK_PREFIX):
            return
        lnk = SYMLINK_PREFIX + lnk

    try:
        os.unlink(fn)
    except OSError as e:
        print("Unable to unlink symlink:", str(e), file=sys.stderr)
        return
    try:
        os.symlink(lnk, fn)
    except OSError as e:
        print("Unable to recreate symlink", fn, '->', lnk + ':', str(e), file=sys.stderr)
        return
    print(fn, '->', lnk)


if __name__ == '__main__':
    our_desc = """\
Adds or removes the %s prefix to/from the start of each symlink's value.
When given the name of a directory, affects all the symlinks in that directory hierarchy.
""" % SYMLINK_PREFIX
    epilog = 'See the "munge symlinks" option in the rsyncd.conf manpage for more details.'
    parser = argparse.ArgumentParser(description=our_desc, epilog=epilog, add_help=False)
    uniq_group = parser.add_mutually_exclusive_group()
    uniq_group.add_argument('--munge', action='store_true', help="Add the prefix to symlinks (the default).")
    uniq_group.add_argument('--unmunge', action='store_true', help="Remove the prefix from symlinks.")
    parser.add_argument('--all', action='store_true', help="Always adds the prefix when munging (even if already munged) or removes multiple instances of the prefix when unmunging.")
    parser.add_argument('--help', '-h', action='help', help="Output this help message and exit.")
    parser.add_argument('names', metavar='NAME', nargs='+', help="One or more directories and/or symlinks to process.")
    args = parser.parse_args()
    main()

# vim: sw=4 et
