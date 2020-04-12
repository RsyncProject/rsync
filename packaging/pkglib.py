import os, sys, re, subprocess

# This python3 library provides a few helpful routines that are
# used by the latest packaging scripts.

# Output the msg args to stderr.  Accepts all the args that print() accepts.
def warn(*msg):
    print(*msg, file=sys.stderr)


# Output the msg args to stderr and die with a non-zero return-code.
# Accepts all the args that print() accepts.
def die(*msg):
    warn(*msg)
    sys.exit(1)


def _tweak_opts(cmd, opts):
    if type(cmd) == str:
        opts['shell'] = True
    if not 'encoding' in opts:
        if opts.get('raw', False):
            del opts['raw']
        else:
            opts['encoding'] = 'utf-8'


# This does a normal subprocess.run() with some auto-args added to make life easier.
def cmd_run(cmd, **opts):
    _tweak_opts(cmd, opts)
    return subprocess.run(cmd, **opts)


# Works like cmd_run() with a default check=True specified for you.
def cmd_chk(cmd, **opts):
    return cmd_run(cmd, check=True, **opts)


# Captures stdout & stderr together in a string and returns the (output, return-code) tuple.
def cmd_txt(cmd, **opts):
    opts['stdout'] = subprocess.PIPE
    opts['stderr'] = subprocess.STDOUT
    _tweak_opts(cmd, opts)
    proc = subprocess.Popen(cmd, **opts)
    out = proc.communicate()[0]
    return (out, proc.returncode)


# Captures stdout & stderr together in a string and returns the output if the command has a 0
# return-code. Otherwise it throws an exception that indicates the return code and all the
# captured output.
def cmd_txt_chk(cmd, **opts):
    out, rc = cmd_txt(cmd, **opts)
    if rc != 0:
        cmd_err = f'Command "{cmd}" returned non-zero exit status "{rc}" and output:\n{out}'
        raise Exception(cmd_err)
    return out


# Starts a piped-output command of just stdout (by default) and leaves it up to you to do
# any incremental reading of the output and to call communicate() on the returned object.
def cmd_pipe(cmd, **opts):
    opts['stdout'] = subprocess.PIPE
    _tweak_opts(cmd, opts)
    return subprocess.Popen(cmd, **opts)


# Runs a "git status" command and dies if the checkout is not clean (the
# arg fatal_unless_clean can be used to make that non-fatal.  Returns a
# tuple of the current branch, the is_clean flag, and the status text.
def check_git_status(fatal_unless_clean=True, subdir='.'):
    status_txt = cmd_txt_chk(f"cd '{subdir}' && git status")
    is_clean = re.search(r'\nnothing to commit.+working (directory|tree) clean', status_txt) != None

    if not is_clean and fatal_unless_clean:
        if subdir == '.':
            subdir = ''
        else:
            subdir = f" *{subdir}*"
        die(f"The{subdir} checkout is not clean:\n" + status_txt)

    m = re.match(r'^(?:# )?On branch (.+)\n', status_txt)
    cur_branch = m[1] if m else None

    return (cur_branch, is_clean, status_txt)


# Calls check_git_status() on the current git checkout and (optionally) a subdir path's
# checkout. Use fatal_unless_clean to indicate if an unclean checkout is fatal or not.
# The master_branch arg indicates what branch we want both checkouts to be using, and
# if the branch is wrong the user is given the option of either switching to the right
# branch or aborting.
def check_git_state(master_branch, fatal_unless_clean=True, check_extra_dir=None):
    cur_branch = check_git_status(fatal_unless_clean)[0]
    branch = re.sub(r'^patch/([^/]+)/[^/]+$', r'\1', cur_branch) # change patch/BRANCH/PATCH_NAME into BRANCH
    if branch != master_branch:
        print(f"The checkout is not on the {master_branch} branch.")
        if master_branch != 'master':
            sys.exit(1)
        ans = input(f"Do you want me to continue with --branch={branch}? [n] ")
        if not ans or not re.match(r'^y', ans, flags=re.I):
            sys.exit(1)
        master_branch = branch

    if check_extra_dir and os.path.isdir(os.path.join(check_extra_dir, '.git')):
        branch = check_git_status(fatal_unless_clean, check_extra_dir)[0]
        if branch != master_branch:
            print(f"The *{check_extra_dir}* checkout is on branch {branch}, not branch {master_branch}.")
            ans = input(f"Do you want to change it to branch {master_branch}? [n] ")
            if not ans or not re.match(r'^y', ans, flags=re.I):
                sys.exit(1)
            subdir.check_call(f"cd {check_extra_dir} && git checkout '{master_branch}'", shell=True)

    return (cur_branch, master_branch)


# Return the git hash of the most recent commit.
def latest_git_hash(branch):
    out = cmd_txt_chk(['git', 'log', '-1', '--no-color', branch])
    m = re.search(r'^commit (\S+)', out, flags=re.M)
    if not m:
        die(f"Unable to determine commit hash for master branch: {branch}")
    return m[1]


# Return a set of all branch names that have the format "patch/BASE_BRANCH/NAME"
# for the given base_branch string.  Just the NAME portion is put into the set.
def get_patch_branches(base_branch):
    branches = set()
    proc = cmd_pipe('git branch -l'.split())
    for line in proc.stdout:
        m = re.search(r' patch/([^/]+)/(.+)', line)
        if m and m[1] == base_branch:
            branches.add(m[2])
    proc.communicate()
    return branches


# Snag the GENFILES values out of the Makefile.in file and return them as a list.
def get_extra_files():
    cont_re = re.compile(r'\\\n')

    extras = [ ]

    with open('Makefile.in', 'r', encoding='utf-8') as fh:
        for line in fh:
            if not extras:
                chk = re.sub(r'^GENFILES=', '', line)
                if line == chk:
                    continue
                line = chk
            m = re.search(r'\\$', line)
            line = re.sub(r'^\s+|\s*\\\n?$|\s+$', '', line)
            extras += line.split()
            if not m:
                break

    return extras

# vim: sw=4 et
