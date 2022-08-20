#!/usr/bin/env python3
# This script outputs either perl or python code that parses all possible options
# that the code in options.c might send to the server.  The resulting code is then
# included in the rrsync script.

import re, argparse

short_no_arg = { }
short_with_num = { '@': 1 }
long_opts = { # These include some extra long-args that BackupPC uses:
        'block-size': 1,
        'daemon': -1,
        'debug': 1,
        'fake-super': 0,
        'fuzzy': 0,
        'group': 0,
        'hard-links': 0,
        'ignore-times': 0,
        'info': 1,
        'links': 0,
        'log-file': 3,
        'munge-links': 0,
        'no-munge-links': -1,
        'one-file-system': 0,
        'owner': 0,
        'perms': 0,
        'recursive': 0,
        'stderr': 1,
        'times': 0,
        'copy-devices': -1,
        'write-devices': -1,
        }

def main():
    last_long_opt = None

    with open('../options.c') as fh:
        for line in fh:
            m = re.search(r"argstr\[x\+\+\] = '([^.ie])'", line)
            if m:
                short_no_arg[m.group(1)] = 1
                last_long_opt = None
                continue

            m = re.search(r'asprintf\([^,]+, "-([a-zA-Z0-9])\%l?[ud]"', line)
            if m:
                short_with_num[m.group(1)] = 1
                last_long_opt = None
                continue

            m = re.search(r'args\[ac\+\+\] = "--([^"=]+)"', line)
            if m:
                last_long_opt = m.group(1)
                if last_long_opt not in long_opts:
                    long_opts[last_long_opt] = 0
                else:
                    last_long_opt = None
                continue

            if last_long_opt:
                m = re.search(r'args\[ac\+\+\] = safe_arg\("", ([^[("\s]+)\);', line)
                if m:
                    long_opts[last_long_opt] = 2
                    last_long_opt = None
                    continue
                if 'args[ac++] = ' in line:
                    last_long_opt = None

            m = re.search(r'return "--([^"]+-dest)";', line)
            if m:
                long_opts[m.group(1)] = 2
                last_long_opt = None
                continue

            m = re.search(r'asprintf\([^,]+, "--([^"=]+)=', line)
            if not m:
                m = re.search(r'args\[ac\+\+\] = "--([^"=]+)=', line)
                if not m:
                    m = re.search(r'args\[ac\+\+\] = safe_arg\("--([^"=]+)"', line)
                    if not m:
                        m = re.search(r'fmt = .*: "--([^"=]+)=', line)
            if m:
                long_opts[m.group(1)] = 1
                last_long_opt = None

    long_opts['files-from'] = 3

    txt = """\
### START of options data produced by the cull-options script. ###

# To disable a short-named option, add its letter to this string:
"""

    txt += str_assign('short_disabled', 's') + "\n"
    txt += '# These are also disabled when the restricted dir is not "/":\n'
    txt += str_assign('short_disabled_subdir', 'KLk') + "\n"
    txt += '# These are all possible short options that we will accept (when not disabled above):\n'
    txt += str_assign('short_no_arg', ''.join(sorted(short_no_arg)), 'DO NOT REMOVE ANY')
    txt += str_assign('short_with_num', ''.join(sorted(short_with_num)), 'DO NOT REMOVE ANY')
   
    txt += """
# To disable a long-named option, change its value to a -1.  The values mean:
# 0 = the option has no arg; 1 = the arg doesn't need any checking; 2 = only
# check the arg when receiving; and 3 = always check the arg.
"""

    print(txt, end='')

    if args.python:
        print("long_opts = {")
        sep = ':'
    else:
        print("our %long_opt = (")
        sep = ' =>'

    for opt in sorted(long_opts):
        if opt.startswith(('min-', 'max-')):
            val = 1
        else:
            val = long_opts[opt]
        print(' ', repr(opt) + sep, str(val) + ',')

    if args.python:
        print("}")
    else:
        print(");")
    print("\n### END of options data produced by the cull-options script. ###")


def str_assign(name, val, comment=None):
    comment = ' # ' + comment if comment else ''
    if args.python:
        return name + ' = ' + repr(val) + comment + "\n"
    return 'our $' + name + ' = ' + repr(val) + ';' + comment + "\n"


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Output culled rsync options for rrsync.", add_help=False)
    out_group = parser.add_mutually_exclusive_group()
    out_group.add_argument('--perl', action='store_true', help="Output perl code.")
    out_group.add_argument('--python', action='store_true', help="Output python code (the default).")
    parser.add_argument('--help', '-h', action='help', help="Output this help message and exit.")
    args = parser.parse_args()
    if not args.perl:
        args.python = True
    main()

# vim: sw=4 et
