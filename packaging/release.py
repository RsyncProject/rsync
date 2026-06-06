#!/usr/bin/env python3

# Step-based release script for rsync.  Each step is a separate invocation
# selected by a --step-N-XX option, so the maintainer drives the release
# manually one piece at a time.
#
# All persistent state and working files live in ../release/ (a sibling of
# the rsync git checkout):
#
#   ../release/rsync-ftp/       mirror of samba.org:/home/ftp/pub/rsync
#   ../release/rsync-html/      release-time snapshot of the html site
#   ../release/work/            scratch space for tarball / diff staging
#   ../release/release-state.json   info shared between steps
#
# The rsync-patches archive is no longer maintained and has been dropped.
#
# Run "packaging/release.py --list" to see the step list.

import os, sys, re, argparse, glob, shutil, json, signal, subprocess
from datetime import datetime

sys.path = ['packaging'] + sys.path

from pkglib import (
    warn, die, cmd_run, cmd_chk, cmd_txt, cmd_txt_chk, cmd_pipe,
    check_git_state, get_rsync_version,
    get_NEWS_version_info, get_protocol_versions,
)

# ---------- Paths ----------

RELEASE_DIR = os.path.realpath('../release')
FTP_DIR     = os.path.join(RELEASE_DIR, 'rsync-ftp')
HTML_DIR    = os.path.join(RELEASE_DIR, 'rsync-html')
WORK_DIR    = os.path.join(RELEASE_DIR, 'work')
STATE_FILE  = os.path.join(RELEASE_DIR, 'release-state.json')

# The rsync-web/ subdirectory in the rsync source tree is the source-of-truth
# for the git-tracked html content.  step-1-fetch snapshots it into HTML_DIR
# for the release flow, where it can be edited or augmented with server-side
# content before step-11-push-html sends it to samba.org.
HTML_SRC = os.path.realpath('rsync-web')

FTP_REMOTE_PATH  = '/home/ftp/pub/rsync'
HTML_REMOTE_PATH = '/home/httpd/html/rsync'

# Files that ./configure + make produce and that the release tarball / diff
# need to bundle alongside the git-tracked source.  Mirrors the GENFILES
# definition in Makefile.in (with rrsync.1{,.html} since we always configure
# --with-rrsync in --step-4-build).
GEN_FILES = [
    'configure.sh',
    'aclocal.m4',
    'config.h.in',
    'rsync.1',     'rsync.1.html',
    'rsync-ssl.1', 'rsync-ssl.1.html',
    'rsyncd.conf.5', 'rsyncd.conf.5.html',
    'rrsync.1',    'rrsync.1.html',
]

# ---------- Step registry ----------

STEPS = [
    ('step-1-fetch',       'mirror ../release/rsync-ftp from samba.org and snapshot ../release/rsync-html from rsync-web/'),
    ('step-2-prepare',     'gather release info interactively and write release-state.json'),
    ('step-3-tweak',       'update version.h, rsync.h, NEWS.md, and packaging/*.spec'),
    ('step-4-build',       'run smart-make + make gen'),
    ('step-5-commit',      'git commit -a (commit the prepared release changes)'),
    ('step-6-tag',         'create the gpg-signed git tag'),
    ('step-7-tarball',     'build the source tarball and diffs.gz against the previous release'),
    ('step-8-update-ftp',  'refresh README/NEWS/INSTALL/html in the ftp dir, regen ChangeLog.gz, gpg-sign tarballs'),
    ('step-9-toplinks',    'hard-link top-level release files (final releases only)'),
    ('step-10-push-ftp',   'rsync ../release/rsync-ftp/ to samba.org'),
    ('step-11-push-html',  'rsync ../release/rsync-html/ to samba.org (after any manual edits)'),
    ('step-12-push-git',   'print the git push commands for you to run'),
]
STEP_FLAGS = [s[0] for s in STEPS]

DASH_LINE = '=' * 74

# ---------- State helpers ----------

def load_state():
    if not os.path.isfile(STATE_FILE):
        die(f"{STATE_FILE} not found.  Run --step-2-prepare first.")
    with open(STATE_FILE, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def save_state(state):
    os.makedirs(RELEASE_DIR, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write('\n')


def require_samba_host():
    host = os.environ.get('RSYNC_SAMBA_HOST', '')
    if not host.endswith('.samba.org'):
        die("Set RSYNC_SAMBA_HOST in your environment to the samba hostname (e.g. hr3.samba.org).")
    return host


def require_top_of_checkout():
    if not os.path.isfile('packaging/release.py'):
        die("Run this script from the top of your rsync checkout.")
    if not os.path.isdir('.git'):
        die("There is no .git dir in the current directory.")


def replace_or_die(regex, repl, txt, die_msg):
    m = regex.search(txt)
    if not m:
        die(die_msg)
    return regex.sub(repl, txt, 1)


def section(title):
    print(f"\n{DASH_LINE}\n== {title}\n{DASH_LINE}")


def confirm(prompt, default_no=True):
    suffix = '[n] ' if default_no else '[y] '
    ans = input(f"{prompt} {suffix}").strip().lower()
    if default_no:
        return ans.startswith('y')
    return ans == '' or ans.startswith('y')


# ---------- Step 1: fetch ftp + html ----------

def step_1_fetch(args):
    host = require_samba_host()
    os.makedirs(RELEASE_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)

    section(f"Fetching ftp dir into {FTP_DIR}")
    if not os.path.isdir(FTP_DIR):
        os.makedirs(FTP_DIR)
    # packaging/ftp.filt is the authoritative copy of the .filt filter file
    # that controls which subtrees rsync excludes from the FTP mirror.
    # Seed FTP_DIR/.filt from it so the bundled version is what step-1's
    # rsync uses here, and so step-10-push-ftp propagates it back to the
    # server.  --exclude=/.filt below stops the server's copy from
    # overwriting our bundled one on the way down.
    filt = os.path.join(FTP_DIR, '.filt')
    bundled_filt = os.path.realpath('packaging/ftp.filt')
    if not os.path.isfile(bundled_filt):
        die(f"{bundled_filt} not found; cannot seed .filt for the FTP pull.")
    shutil.copyfile(bundled_filt, filt)
    cmd_chk(['rsync', '-aivOHP', f'-f:_{filt}', '--exclude=/.filt',
             f'{host}:{FTP_REMOTE_PATH}/', f'{FTP_DIR}/'])

    section(f"Snapshotting html dir from {HTML_SRC} into {HTML_DIR}")
    if not os.path.isdir(HTML_SRC):
        die(f"{HTML_SRC} not found.  This should be the in-tree rsync-web/ "
            f"subdirectory; something is wrong with your checkout.")
    os.makedirs(HTML_DIR, exist_ok=True)
    cmd_chk(['rsync', '-aiv', f'{HTML_SRC}/', f'{HTML_DIR}/'])

    # Then mirror non-git html content from the server, skipping files that
    # the html git already provides (driven by the 'filt' file in HTML_DIR).
    filt = os.path.join(HTML_DIR, 'filt')
    if os.path.exists(filt):
        tmp_filt = os.path.join(HTML_DIR, 'tmp-filt')
        cmd_chk(f"sed -n -e 's/[-P]/H/p' '{filt}' >'{tmp_filt}'")
        cmd_chk(['rsync', '-aivOHP', f'-f._{tmp_filt}',
                 f'{host}:{HTML_REMOTE_PATH}/', f'{HTML_DIR}/'])
        os.unlink(tmp_filt)

    print(f"\nFetch complete.  Local dirs are now in {RELEASE_DIR}.")


# ---------- Step 2: prepare ----------

def step_2_prepare(args):
    require_top_of_checkout()
    os.makedirs(RELEASE_DIR, exist_ok=True)

    if not os.path.isdir(FTP_DIR):
        die(f"{FTP_DIR} does not exist.  Run --step-1-fetch first.")

    now = datetime.now().astimezone()
    cl_today = now.strftime('* %a %b %d %Y')
    year     = now.strftime('%Y')
    ztoday   = now.strftime('%d %b %Y')
    today    = ztoday.lstrip('0')
    tz_now   = now.strftime('%z')
    tz_num   = tz_now[0:1].replace('+', '') + str(float(tz_now[1:3]) + float(tz_now[3:]) / 60)

    curversion = get_rsync_version()
    lastversion, last_protocol_version, pdate = get_NEWS_version_info()
    protocol_version, subprotocol_version    = get_protocol_versions()

    # Default next version: bump preN, or move dev -> pre1.
    version = curversion
    m = re.search(r'pre(\d+)', version)
    if m:
        version = re.sub(r'pre\d+', 'pre' + str(int(m[1]) + 1), version)
    else:
        version = version.replace('dev', 'pre1')

    print(f"\nCurrent version (version.h): {curversion}")
    print(f"Last released version (NEWS.md): {lastversion}")
    print(f"Current protocol version: {protocol_version}  (last released: {last_protocol_version})")

    ans = input(f"\nVersion to release [{version}, '.' to drop the preN suffix]: ").strip()
    if ans == '.':
        version = re.sub(r'pre\d+', '', version)
    elif ans:
        version = ans
    if not re.match(r'^[\d.]+(pre\d+)?$', version):
        die(f'Invalid version: "{version}"')
    version = re.sub(r'[-.]*pre[-.]*', 'pre', version)

    if 'pre' in version and not curversion.endswith('dev'):
        lastversion = curversion

    ans = input(f"Previous version to diff against [{lastversion}]: ").strip()
    if ans:
        lastversion = ans
    lastversion = re.sub(r'[-.]*pre[-.]*', 'pre', lastversion)

    m = re.search(r'(pre\d+)', version)
    pre = m[1] if m else ''
    finalversion = re.sub(r'pre\d+', '', version)

    release = '0.1' if pre else '1'
    ans = input(f"RPM release number [{release}]: ").strip()
    if ans:
        release = ans
    if pre:
        release += '.' + pre

    proto_changed = protocol_version != last_protocol_version
    if proto_changed:
        if finalversion in pdate:
            proto_change_date = pdate[finalversion]
        else:
            while True:
                ans = input(f"Date the protocol changed to {protocol_version} (dd Mmm yyyy): ").strip()
                if re.match(r'^\d\d \w\w\w \d\d\d\d$', ans):
                    break
            proto_change_date = ans
    else:
        proto_change_date = ' ' * 11

    if 'pre' in lastversion:
        if not pre:
            die("Refusing to diff a release version against a pre-release version.")
        srcdir = srcdiffdir = lastsrcdir = 'src-previews'
    elif pre:
        srcdir = srcdiffdir = 'src-previews'
        lastsrcdir = 'src'
    else:
        srcdir = lastsrcdir = 'src'
        srcdiffdir = 'src-diffs'

    state = {
        'version':            version,
        'lastversion':        lastversion,
        'finalversion':       finalversion,
        'pre':                pre,
        'release':            release,
        'protocol_version':   protocol_version,
        'subprotocol_version': subprotocol_version,
        'proto_changed':      proto_changed,
        'proto_change_date':  proto_change_date,
        'srcdir':             srcdir,
        'srcdiffdir':         srcdiffdir,
        'lastsrcdir':         lastsrcdir,
        'today':              today,
        'ztoday':             ztoday,
        'cl_today':           cl_today,
        'year':               year,
        'tz_num':             tz_num,
        'master_branch':      args.master_branch,
    }
    save_state(state)

    section("Release info")
    for k in ('version', 'lastversion', 'release', 'srcdir', 'srcdiffdir', 'lastsrcdir',
              'protocol_version', 'proto_changed', 'proto_change_date'):
        print(f"  {k}: {state[k]}")
    print(f"\nWrote {STATE_FILE}.  Re-run --step-2-prepare to change anything.")


# ---------- Step 3: tweak version files ----------

def step_3_tweak(args):
    require_top_of_checkout()
    state = load_state()

    version          = state['version']
    finalversion     = state['finalversion']
    pre              = state['pre']
    release          = state['release']
    today            = state['today']
    ztoday           = state['ztoday']
    cl_today         = state['cl_today']
    year             = state['year']
    tz_num           = state['tz_num']
    proto_changed    = state['proto_changed']
    proto_change_date = state['proto_change_date']
    protocol_version  = state['protocol_version']
    srcdir           = state['srcdir']

    specvars = {
        'Version:':           finalversion,
        'Release:':           release,
        '%define fullversion': f'%{{version}}{pre}',
        'Released':           version + '.',
        '%define srcdir':     srcdir,
    }

    tweak_files = ['version.h', 'rsync.h', 'NEWS.md']
    tweak_files += glob.glob('packaging/*.spec')
    tweak_files += glob.glob('packaging/*/*.spec')

    for fn in tweak_files:
        with open(fn, 'r', encoding='utf-8') as fh:
            old_txt = txt = fh.read()
        if fn == 'version.h':
            x_re = re.compile(r'^(#define RSYNC_VERSION).*', re.M)
            txt = replace_or_die(x_re, r'\1 "%s"' % version, txt,
                                 f"Unable to update RSYNC_VERSION in {fn}")
            x_re = re.compile(r'^(#define MAINTAINER_TZ_OFFSET).*', re.M)
            txt = replace_or_die(x_re, r'\1 ' + tz_num, txt,
                                 f"Unable to update MAINTAINER_TZ_OFFSET in {fn}")
        elif fn == 'rsync.h':
            x_re = re.compile(r'(#define\s+SUBPROTOCOL_VERSION)\s+(\d+)')
            repl = lambda m: m[1] + ' ' + (
                '0' if not pre or not proto_changed
                else '1' if m[2] == '0'
                else m[2])
            txt = replace_or_die(x_re, repl, txt,
                                 f"Unable to find SUBPROTOCOL_VERSION in {fn}")
        elif fn == 'NEWS.md':
            efv = re.escape(finalversion)
            x_re = re.compile(
                r'^# NEWS for rsync %s \(UNRELEASED\)\s+## Changes in this version:\n' % efv
                + r'(\n### PROTOCOL NUMBER:\s+- The protocol number was changed to \d+\.\n)?')
            rel_day = 'UNRELEASED' if pre else today
            repl = (f'# NEWS for rsync {finalversion} ({rel_day})\n\n'
                    + '## Changes in this version:\n')
            if proto_changed:
                repl += f'\n### PROTOCOL NUMBER:\n\n - The protocol number was changed to {protocol_version}.\n'
            good_top = re.sub(r'\(.*?\)', '(UNRELEASED)', repl, 1)
            msg = (f"The top of {fn} is not in the right format.  It should be:\n" + good_top)
            txt = replace_or_die(x_re, repl, txt, msg)
            x_re = re.compile(
                r'^(\| )(\S{2} \S{3} \d{4})(\s+\|\s+%s\s+\| ).{11}(\s+\| )\S{2}(\s+\|+)$' % efv,
                re.M)
            repl = lambda m: (m[1] + (m[2] if pre else ztoday) + m[3]
                              + proto_change_date + m[4] + protocol_version + m[5])
            txt = replace_or_die(x_re, repl, txt,
                                 f'Unable to find "| ?? ??? {year} | {finalversion} | ... |" line in {fn}')
        elif '.spec' in fn:
            for var, val in specvars.items():
                x_re = re.compile(r'^%s .*' % re.escape(var), re.M)
                txt = replace_or_die(x_re, var + ' ' + val, txt,
                                     f"Unable to update {var} in {fn}")
            x_re = re.compile(r'^\* \w\w\w \w\w\w \d\d \d\d\d\d (.*)', re.M)
            txt = replace_or_die(x_re, r'%s \1' % cl_today, txt,
                                 f"Unable to update ChangeLog header in {fn}")
        else:
            die(f"Unrecognized file in tweak_files: {fn}")

        if txt != old_txt:
            print(f"Updating {fn}")
            with open(fn, 'w', encoding='utf-8') as fh:
                fh.write(txt)

    cmd_chk(['packaging/year-tweak'])

    section("git diff after tweaks")
    cmd_run(['git', '--no-pager', 'diff'])


# ---------- Step 4: build ----------

def step_4_build(args):
    require_top_of_checkout()
    load_state()  # just to ensure we've prepared

    section("Running prepare-source + configure --prefix=/usr --with-rrsync + make + make gen")
    # Always re-prepare so configure.sh is current; we run configure ourselves
    # with the release-required flags rather than relying on the cached
    # config.status (which may have been produced with different options).
    if os.path.isfile('.fetch'):
        cmd_chk(['./prepare-source', 'fetch'])
    else:
        cmd_chk(['./prepare-source'])

    cmd_chk(['./configure', '--prefix=/usr', '--with-rrsync'])
    cmd_chk(['make'])
    cmd_chk(['make', 'gen'])


# ---------- Step 5: commit ----------

def step_5_commit(args):
    require_top_of_checkout()
    state = load_state()
    version = state['version']

    section("git status")
    cmd_run(['git', 'status'])
    if not confirm("Commit all current changes with the release message?"):
        die("Aborted.")
    cmd_chk(['git', 'commit', '-a', '-m', f'Preparing for release of {version} [buildall]'])


# ---------- Step 6: tag ----------

def step_6_tag(args):
    require_top_of_checkout()
    state = load_state()
    version = state['version']
    v_ver = 'v' + version

    out = cmd_txt_chk(['git', 'tag', '-l', v_ver]).out
    if out.strip():
        if not confirm(f"Tag {v_ver} already exists.  Delete and recreate?"):
            die("Aborted.")
        cmd_chk(['git', 'tag', '-d', v_ver])

    # Prime the gpg agent so the actual tag signing won't prompt.
    section("Priming gpg agent")
    cmd_run("touch TeMp; gpg --sign TeMp; rm -f TeMp TeMp.gpg")

    section(f"Creating signed tag {v_ver}")
    out = cmd_txt(['git', 'tag', '-s', '-m', f'Version {version}.', v_ver],
                  capture='combined').out
    print(out, end='')
    if 'bad passphrase' in out.lower() or 'failed' in out.lower():
        die("Tag creation failed.")


# ---------- Step 7: tarball + diff ----------

def step_7_tarball(args):
    require_top_of_checkout()
    state = load_state()

    version      = state['version']
    lastversion  = state['lastversion']
    pre          = state['pre']
    srcdir       = state['srcdir']
    srcdiffdir   = state['srcdiffdir']
    lastsrcdir   = state['lastsrcdir']

    rsync_ver     = 'rsync-' + version
    rsync_lastver = 'rsync-' + lastversion
    v_ver         = 'v' + version

    srctar_name = f"{rsync_ver}.tar.gz"
    diff_name   = f"{rsync_lastver}-{version}.diffs.gz"

    srctar_file = os.path.join(FTP_DIR, srcdir, srctar_name)
    diff_file   = os.path.join(FTP_DIR, srcdiffdir, diff_name)
    lasttar_file = os.path.join(FTP_DIR, lastsrcdir, rsync_lastver + '.tar.gz')

    for d in (os.path.dirname(srctar_file), os.path.dirname(diff_file)):
        os.makedirs(d, exist_ok=True)
    if not os.path.isfile(lasttar_file):
        die(f"Previous tarball not found: {lasttar_file}")

    # Stage in ../release/work to keep the source checkout clean.
    if os.path.isdir(WORK_DIR):
        shutil.rmtree(WORK_DIR)
    os.makedirs(WORK_DIR)

    a_dir = os.path.join(WORK_DIR, 'a')
    b_dir = os.path.join(WORK_DIR, 'b')

    # Extract gen files from the previous tarball into work/a/.
    tweaked_gen_files = [os.path.join(rsync_lastver, fn) for fn in GEN_FILES]
    cmd_chk(['tar', '-C', WORK_DIR, '-xzf', lasttar_file, *tweaked_gen_files])
    os.rename(os.path.join(WORK_DIR, rsync_lastver), a_dir)

    # Copy current gen files (built in the top-level checkout) into work/b/.
    os.makedirs(b_dir)
    cmd_chk(['rsync', '-a', *GEN_FILES, b_dir + '/'])

    section(f"Creating {diff_file}")
    sed_script = r's:^((---|\+\+\+) [ab]/[^\t]+)\t.*:\1:'  # no single quotes!
    cmd_chk(
        f"(git diff v{lastversion} {v_ver} -- ':!.github'; "
        f"diff -upN {a_dir} {b_dir} | sed -r '{sed_script}') | gzip -9 >{diff_file}")

    section(f"Creating {srctar_file}")
    # Reuse work/b/ (which already holds the fresh gen files) as the release
    # staging dir, then let "git archive" overlay the git-tracked source files
    # on top.  That way the tarball ends up with both gen files and source.
    rsync_ver_dir = os.path.join(WORK_DIR, rsync_ver)
    shutil.rmtree(a_dir)
    os.rename(b_dir, rsync_ver_dir)
    cmd_chk(f"git archive --format=tar --prefix={rsync_ver}/ {v_ver} | "
            f"tar -C {WORK_DIR} -xf -")
    cmd_chk(f"support/git-set-file-times --quiet --prefix={rsync_ver_dir}/")
    cmd_chk(['fakeroot', 'tar', '-C', WORK_DIR, '-czf', srctar_file,
             '--exclude=.github', rsync_ver])

    # Leave staging in place; --step-8-update-ftp does its own thing.
    print(f"\nCreated:\n  {srctar_file}\n  {diff_file}")


# ---------- Step 8: update ftp ----------

def step_8_update_ftp(args):
    require_top_of_checkout()
    state = load_state()

    version     = state['version']
    lastversion = state['lastversion']
    srcdir      = state['srcdir']
    srcdiffdir  = state['srcdiffdir']

    rsync_ver     = 'rsync-' + version
    rsync_lastver = 'rsync-' + lastversion
    srctar_file   = os.path.join(FTP_DIR, srcdir, f"{rsync_ver}.tar.gz")
    diff_file     = os.path.join(FTP_DIR, srcdiffdir,
                                 f"{rsync_lastver}-{version}.diffs.gz")

    section(f"Refreshing top-of-tree files in {FTP_DIR}")
    md_files = ['README.md', 'NEWS.md', 'INSTALL.md']
    html_files = [fn for fn in GEN_FILES if fn.endswith('.html')]
    cmd_chk(['rsync', '-a', *md_files, *html_files, FTP_DIR + '/'])
    cmd_chk(['./md-convert', '--dest', FTP_DIR, *md_files])

    section(f"Regenerating {FTP_DIR}/ChangeLog.gz")
    cmd_chk(f"git log --name-status | gzip -9 >{FTP_DIR}/ChangeLog.gz")

    # Prime gpg agent and then sign the tar + diff.
    section("Priming gpg agent")
    cmd_run("touch TeMp; gpg --sign TeMp; rm -f TeMp TeMp.gpg")

    for fn in (srctar_file, diff_file):
        if not os.path.isfile(fn):
            die(f"Missing file to sign: {fn}.  Did --step-7-tarball run successfully?")
        asc_fn = fn + '.asc'
        if os.path.lexists(asc_fn):
            os.unlink(asc_fn)
        section(f"GPG-signing {fn}")
        res = cmd_run(['gpg', '--batch', '-ba', fn])
        if res.returncode not in (0, 2):
            die("gpg signing failed.")


# ---------- Step 9: top-level hard links ----------

def step_9_toplinks(args):
    require_top_of_checkout()
    state = load_state()

    pre = state['pre']
    if pre:
        print("Skipping: pre-releases do not get top-level hard links.")
        return

    version     = state['version']
    lastversion = state['lastversion']
    srcdir      = state['srcdir']
    srcdiffdir  = state['srcdiffdir']

    rsync_ver     = 'rsync-' + version
    rsync_lastver = 'rsync-' + lastversion
    srctar_file = os.path.join(FTP_DIR, srcdir, f"{rsync_ver}.tar.gz")
    diff_file   = os.path.join(FTP_DIR, srcdiffdir,
                               f"{rsync_lastver}-{version}.diffs.gz")

    section("Removing stale top-level rsync-* files")
    for find in [f'{FTP_DIR}/rsync-*.gz',
                 f'{FTP_DIR}/rsync-*.asc',
                 f'{FTP_DIR}/src-previews/rsync-*diffs.gz*']:
        for fn in glob.glob(find):
            os.unlink(fn)

    top_link = [
        srctar_file, srctar_file + '.asc',
        diff_file,   diff_file + '.asc',
    ]
    for fn in top_link:
        target = re.sub(r'/src(-\w+)?/', '/', fn)
        if os.path.lexists(target):
            os.unlink(target)
        os.link(fn, target)
        print(f"  linked {target}")


# ---------- Step 10: push ftp ----------

def step_10_push_ftp(args):
    host = require_samba_host()
    if not os.path.isdir(FTP_DIR):
        die(f"{FTP_DIR} does not exist.  Run --step-1-fetch first.")
    section(f"rsync ftp dir to {host}")
    rsync_with_confirm(['-aivOHP', '--chown=:rsync', '--del',
                        f'-f._{os.path.join(FTP_DIR, ".filt")}',
                        f'{FTP_DIR}/', f'{host}:{FTP_REMOTE_PATH}/'])


# ---------- Step 11: push html ----------

def step_11_push_html(args):
    host = require_samba_host()
    if not os.path.isdir(HTML_DIR):
        die(f"{HTML_DIR} does not exist.  Run --step-1-fetch first.")
    section(f"rsync html dir to {host}")
    filt = os.path.join(HTML_DIR, 'filt')
    rsync_with_confirm(['-aivOHP', '--chown=:rsync', '--del',
                        f'-f._{filt}',
                        f'{HTML_DIR}/', f'{host}:{HTML_REMOTE_PATH}/'])


# ---------- Step 12: print push-git instructions ----------

def step_12_push_git(args):
    state = load_state()
    version = state['version']
    master_branch = state['master_branch']
    v_ver = 'v' + version

    print(f"""\
{DASH_LINE}
Run these from the rsync-git checkout (this script does not push for you):

    git push origin {master_branch}
    git push origin {v_ver}

If you have a 'samba' remote configured (git.samba.org:/data/git/rsync.git):

    git push samba {master_branch}
    git push samba {v_ver}

Then upload the tarball + .asc to the GitHub release for {v_ver},
and announce on rsync-announce@, rsync@, and Discord.
""")


# ---------- shared rsync-with-confirm ----------

def rsync_with_confirm(rsync_args):
    """Run an rsync command in dry-run mode, then ask before running for real."""
    cmd_run(['rsync', '--dry-run', *rsync_args])
    if confirm("Run without --dry-run?"):
        cmd_run(['rsync', *rsync_args])


# ---------- dispatch ----------

STEP_FUNCS = {
    'step-1-fetch':       step_1_fetch,
    'step-2-prepare':     step_2_prepare,
    'step-3-tweak':       step_3_tweak,
    'step-4-build':       step_4_build,
    'step-5-commit':      step_5_commit,
    'step-6-tag':         step_6_tag,
    'step-7-tarball':     step_7_tarball,
    'step-8-update-ftp':  step_8_update_ftp,
    'step-9-toplinks':    step_9_toplinks,
    'step-10-push-ftp':   step_10_push_ftp,
    'step-11-push-html':  step_11_push_html,
    'step-12-push-git':   step_12_push_git,
}


def signal_handler(sig, frame):
    die("\nAborting due to SIGINT.")


def main():
    parser = argparse.ArgumentParser(
        description="Step-based release script for rsync.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run --list to see the steps.  Each invocation runs exactly one --step-* option.")
    parser.add_argument('--branch', '-b', dest='master_branch', default='master',
                        help="The branch to release (default: master).")
    parser.add_argument('--list', action='store_true',
                        help="List all release steps and exit.")
    grp = parser.add_mutually_exclusive_group()
    for flag, descr in STEPS:
        grp.add_argument('--' + flag, dest='step', action='store_const',
                         const=flag, help=descr)
    args = parser.parse_args()

    if args.list:
        print("Release steps:")
        for flag, descr in STEPS:
            print(f"  --{flag:18s} {descr}")
        return

    if not args.step:
        parser.error("pick one --step-N-XX option (or --list to see them).")

    signal.signal(signal.SIGINT, signal_handler)
    os.environ['LESS'] = 'mqeiXR'
    STEP_FUNCS[args.step](args)


if __name__ == '__main__':
    main()

# vim: sw=4 et ft=python
