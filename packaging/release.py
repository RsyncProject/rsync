#!/usr/bin/env python3

# Phase-based release script for rsync.  The prepare-pr phase updates tracked
# release files for review.  After that PR is merged, stage-release creates
# the signed tag and release artefacts without publishing them.  The final
# publish-release phase updates the public mirrors after confirmation.
#
# Persistent state and working files live in a release/ directory beside the
# primary rsync checkout. Worktrees share that directory. RSYNC_RELEASE_DIR
# can override it.
#
#   ../release/rsync-ftp/       mirror of samba.org:/home/ftp/pub/rsync
#   ../release/rsync-html/      release-time snapshot of the html site
#   ../release/work/            scratch space for tarball / diff staging
#   ../release/release-state.json   info shared between steps
#
# The rsync-patches archive is no longer maintained and has been dropped.
#
# Run "packaging/release.py --list" to see the step list.

import os, sys, re, argparse, glob, shutil, json, signal, subprocess, filecmp
from datetime import datetime

sys.path = ['packaging'] + sys.path
from source_archive import GEN_FILES, make_archive

from pkglib import (
    warn, die, cmd_run, cmd_chk, cmd_txt, cmd_txt_chk, cmd_pipe,
    get_rsync_version,
    get_NEWS_version_info, get_protocol_versions,
)

# ---------- Paths ----------

def default_release_dir():
    override = os.environ.get('RSYNC_RELEASE_DIR')
    if override:
        return os.path.realpath(override)
    checkout = os.path.realpath('.')
    project_dir = os.path.dirname(checkout)
    if os.path.basename(project_dir) == 'worktrees':
        project_dir = os.path.dirname(project_dir)
    try:
        proc = subprocess.run(
            ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            check=True, capture_output=True, encoding='utf-8')
        common_dir = os.path.realpath(proc.stdout.strip())
        return os.path.realpath(os.path.join(os.path.dirname(common_dir), '..', 'release'))
    except (OSError, subprocess.SubprocessError):
        return os.path.join(project_dir, 'release')


RELEASE_DIR = default_release_dir()
FTP_DIR     = os.path.join(RELEASE_DIR, 'rsync-ftp')
HTML_DIR    = os.path.join(RELEASE_DIR, 'rsync-html')
WORK_DIR    = os.path.join(RELEASE_DIR, 'work')
STATE_FILE  = os.path.join(RELEASE_DIR, 'release-state.json')

# The rsync-web/ subdirectory in the rsync source tree is the source-of-truth
# for the git-tracked html content.  stage-release snapshots it into HTML_DIR
# for the release flow and augments it with filtered server-side content.
HTML_SRC = os.path.realpath('rsync-web')

FTP_REMOTE_PATH  = '/home/ftp/pub/rsync'
HTML_REMOTE_PATH = '/home/httpd/html/rsync'

# ---------- Phase registry ----------

PHASES = [
    ('prepare-pr',      'update tracked release files and run the release build'),
    ('stage-release',   'fetch mirrors, verify the merged PR, tag, build and sign artefacts'),
    ('publish-release', 'publish the FTP and website trees and print final release actions'),
]

DASH_LINE = '=' * 74

# ---------- State helpers ----------

def load_state():
    if not os.path.isfile(STATE_FILE):
        die(f"{STATE_FILE} not found. Run --prepare-pr first.")
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
    if not os.path.exists('.git'):
        die("There is no .git in the current directory (run from the top of a git checkout or worktree).")


def replace_or_die(regex, repl, txt, die_msg):
    m = regex.search(txt)
    if not m:
        die(die_msg)
    return regex.sub(repl, txt, 1)


def section(title):
    print(f"\n{DASH_LINE}\n== {title}\n{DASH_LINE}")


def verify_html_snapshot():
    tracked = cmd_txt_chk(['git', 'ls-files', '-z', '--', 'rsync-web']).out.split('\0')
    for source in filter(None, tracked):
        relative = os.path.relpath(os.path.realpath(source), HTML_SRC)
        staged = os.path.join(HTML_DIR, relative)
        if not os.path.isfile(staged) or not filecmp.cmp(source, staged, shallow=False):
            die(f"Staged website file differs from reviewed source: {source}")


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
    # Seed FTP_DIR/.filt from it so the bundled version is used for the pull
    # and later propagated back to the server during publication.
    # --exclude=/.filt below stops the server's copy from
    # overwriting our bundled one on the way down.
    filt = os.path.join(FTP_DIR, '.filt')
    bundled_filt = os.path.realpath('packaging/ftp.filt')
    if not os.path.isfile(bundled_filt):
        die(f"{bundled_filt} not found; cannot seed .filt for the FTP pull.")
    shutil.copyfile(bundled_filt, filt)
    cmd_chk(['rsync', '-aivOHP', f'-f:_{filt}', '--exclude=/.filt',
             f'{host}:{FTP_REMOTE_PATH}/', f'{FTP_DIR}/'])

    section(f"Fetching server-only html content into {HTML_DIR}")
    if not os.path.isdir(HTML_SRC):
        die(f"{HTML_SRC} not found.  This should be the in-tree rsync-web/ "
            f"subdirectory; something is wrong with your checkout.")
    if os.path.isdir(HTML_DIR):
        shutil.rmtree(HTML_DIR)
    os.makedirs(HTML_DIR, exist_ok=True)

    # Mirror non-git content from the server first. The reviewed rsync-web
    # source is overlaid afterwards and therefore remains authoritative.
    filt = os.path.join(HTML_SRC, 'filt')
    if os.path.exists(filt):
        tmp_filt = os.path.join(WORK_DIR, 'html-pull.filt')
        cmd_chk(f"sed -n -e 's/[-P]/H/p' '{filt}' >'{tmp_filt}'")
        cmd_chk(['rsync', '-aivOHP', f'-f._{tmp_filt}',
                 f'{host}:{HTML_REMOTE_PATH}/', f'{HTML_DIR}/'])
        os.unlink(tmp_filt)

    section(f"Overlaying reviewed html from {HTML_SRC}")
    cmd_chk(['rsync', '-aiv', f'{HTML_SRC}/', f'{HTML_DIR}/'])
    verify_html_snapshot()

    print(f"\nFetch complete.  Local dirs are now in {RELEASE_DIR}.")


# ---------- Step 2: prepare ----------

def step_2_prepare(args):
    require_top_of_checkout()
    os.makedirs(RELEASE_DIR, exist_ok=True)

    now = datetime.now().astimezone()
    cl_today = now.strftime('* %a %b %d %Y')
    year     = now.strftime('%Y')
    ztoday   = now.strftime('%d %b %Y')
    today    = ztoday.lstrip('0')
    web_today = f'{now.day} {now.strftime("%B %Y")}'
    tz_now   = now.strftime('%z')
    tz_num   = tz_now[0:1].replace('+', '') + str(float(tz_now[1:3]) + float(tz_now[3:]) / 60)

    curversion = get_rsync_version()
    with open('NEWS.md', 'r', encoding='utf-8') as fh:
        news_top = fh.readline()
    m = re.match(r'^# NEWS for rsync (\d+\.\d+\.\d+)', news_top)
    news_version = m[1] if m else None
    # Skip the version we are releasing: its NEWS entry may already be dated,
    # in which case it would otherwise be taken for the previous release.
    lastversion, last_protocol_version, pdate = get_NEWS_version_info(
        skip_version=news_version or re.sub(r'(pre\d+|dev)$', '', curversion))
    protocol_version, subprotocol_version    = get_protocol_versions()

    # Default next version: bump preN, or move dev -> pre1.
    version = curversion
    m = re.search(r'pre(\d+)', version)
    if m:
        version = re.sub(r'pre\d+', 'pre' + str(int(m[1]) + 1), version)
    else:
        version = version.replace('dev', 'pre1')
        if version == curversion and news_version and news_version != curversion:
            version = news_version

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
        'web_today':          web_today,
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
    print(f"\nWrote {STATE_FILE}. Re-run --prepare-pr to change anything.")


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
    if not pre:
        tweak_files += ['rsync-web/index.html', 'rsync-web/security.html']
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
            # Accept either "(UNRELEASED)" or an already-filled date, so a
            # release entry that was dated by hand (or by an earlier run of
            # this step) does not have to be reverted before releasing.
            x_re = re.compile(
                r'^# NEWS for rsync %s \((?:UNRELEASED|\?\? \?\?\? \d{4}|\d+ \w{3} \d{4})\)\s+## Changes in this version:\n' % efv
                + r'(\n### PROTOCOL NUMBER:\s+- The protocol number was changed to \d+\.\n)?')
            rel_day = 'UNRELEASED' if pre else today
            repl = (f'# NEWS for rsync {finalversion} ({rel_day})\n\n'
                    + '## Changes in this version:\n')
            if proto_changed:
                repl += f'\n### PROTOCOL NUMBER:\n\n - The protocol number was changed to {protocol_version}.\n'
            good_top = re.sub(r'\(.*?\)', '(UNRELEASED)', repl, 1)
            msg = (f"The top of {fn} is not in the right format.  It should be:\n" + good_top
                   + "(an already-filled release date in place of UNRELEASED is also accepted)")
            txt = replace_or_die(x_re, repl, txt, msg)
            x_re = re.compile(
                r'^(\| )(\S{2} \S{3} \d{4})(\s+\|\s+%s\s+\| ).{11}(\s+\| )\S{2}(\s+\|+)$' % efv,
                re.M)
            repl = lambda m: (m[1] + (m[2] if pre else ztoday) + m[3]
                              + proto_change_date + m[4] + protocol_version + m[5])
            txt = replace_or_die(x_re, repl, txt,
                                 f'Unable to find "| ?? ??? {year} | {finalversion} | ... |" line in {fn}')
        elif fn in ('rsync-web/index.html', 'rsync-web/security.html'):
            heading = (f'Rsync version {version} released'
                       if fn.endswith('index.html') else f'Security fix in {version}')
            x_re = re.compile(
                rf'(<h3>{re.escape(heading)}</h3>\s*'
                r'<i class=date>)(?:\?\? \?\?\? \d{4}|\d+ \w+ \d{4})(</i>)')
            txt = replace_or_die(
                x_re, rf'\g<1>{state["web_today"]}\2', txt,
                f'Unable to find the {version} release date in {fn}')
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


# ---------- Step 5: verify merged release source ----------

def step_5_verify(args):
    require_top_of_checkout()
    state = load_state()
    version = state['version']
    finalversion = state['finalversion']
    lastversion = state['lastversion']
    pre = state['pre']
    master_branch = state['master_branch']

    branch = cmd_txt_chk(['git', 'branch', '--show-current']).out.strip()
    if branch != master_branch:
        die(f"Release staging requires branch {master_branch}, not {branch}.")
    dirty = cmd_txt_chk(['git', 'status', '--porcelain=v1']).out
    if dirty:
        die("Release staging requires a clean checkout:\n" + dirty)
    cmd_chk(['git', 'fetch', 'origin', master_branch])
    head = cmd_txt_chk(['git', 'rev-parse', 'HEAD']).out.strip()
    upstream = cmd_txt_chk(['git', 'rev-parse', f'origin/{master_branch}']).out.strip()
    if head != upstream:
        die(f"Release commit {head} does not match origin/{master_branch} at {upstream}.")

    if get_rsync_version() != version:
        die(f"version.h reports {get_rsync_version()}, expected {version}.")
    if not pre:
        release_day = datetime.now().astimezone().strftime('%d %b %Y').lstrip('0')
        if state['today'] != release_day:
            die(f"Release metadata is dated {state['today']}, not {release_day}. "
                "Prepare a corrected release PR before staging.")

    expected_news = (f'# NEWS for rsync {finalversion} (UNRELEASED)'
                     if pre else f'# NEWS for rsync {finalversion} ({state["today"]})')
    checks = {
        'NEWS.md': [expected_news],
        'packaging/lsb/rsync.spec': [f'Version: {finalversion}', f'Released {version}.'],
    }
    if not pre:
        checks['rsync-web/index.html'] = [
            f'Rsync version {version} released',
            f'<i class=date>{state["web_today"]}</i>',
            f'rsync-{version}.tar.gz',
            f'rsync-{lastversion}-{version}.diffs.gz',
        ]
        checks['rsync-web/security.html'] = [
            f'Security fix in {version}',
            f'<i class=date>{state["web_today"]}</i>',
            'CVE-2026-119741',
        ]

    for fn, required in checks.items():
        with open(fn, 'r', encoding='utf-8') as fh:
            txt = fh.read()
        for value in required:
            if value not in txt:
                die(f'{fn} does not contain the expected release text: {value}')

    section(f"Release source verified for {version}")


def release_preflight():
    missing = [command for command in ('fakeroot', 'git', 'gpg', 'gzip', 'rsync', 'sed', 'tar')
               if not shutil.which(command)]
    if missing:
        die("Missing release commands: " + ', '.join(missing))
    section("Checking release markdown conversion")
    cmd_chk(['./md-convert', '--test', 'NEWS.md'])


def verify_artefact_inputs():
    state = load_state()
    required = list(GEN_FILES)
    required.append(os.path.join(
        FTP_DIR, state['lastsrcdir'], f'rsync-{state["lastversion"]}.tar.gz'))
    missing = [fn for fn in required if not os.path.isfile(fn)]
    if missing:
        die("Missing release inputs:\n  " + '\n  '.join(missing))


# ---------- Step 6: tag ----------

def step_6_tag(args):
    require_top_of_checkout()
    state = load_state()
    version = state['version']
    v_ver = 'v' + version

    head = cmd_txt_chk(['git', 'rev-parse', 'HEAD']).out.strip()
    out = cmd_txt_chk(['git', 'tag', '-l', v_ver]).out
    if out.strip():
        target = cmd_txt_chk(['git', 'rev-list', '-n', '1', v_ver]).out.strip()
        if target != head:
            die(f"Tag {v_ver} already exists at {target}, not release commit {head}.")
        section(f"Reusing signed tag {v_ver}")
        cmd_chk(['git', 'tag', '-v', v_ver])
        return

    remote = cmd_txt(['git', 'ls-remote', '--exit-code', '--tags', 'origin',
                      f'refs/tags/{v_ver}'], capture='combined')
    if remote.rc == 0:
        die(f"Tag {v_ver} already exists on origin. Refusing to recreate it.")
    if remote.rc != 2:
        die(f"Unable to check whether {v_ver} exists on origin:\n{remote.out}")

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
    make_archive(v_ver, rsync_ver_dir, srctar_file)

    # Leave staging in place for the FTP refresh.
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
            die(f"Missing file to sign: {fn}. The artefact build did not complete.")
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
        die(f"{FTP_DIR} does not exist. Run --stage-release first.")
    section(f"rsync ftp dir to {host}")
    rsync_with_confirm(['-aivOHP', '--chown=:rsync', '--del',
                        f'-f._{os.path.join(FTP_DIR, ".filt")}',
                        f'{FTP_DIR}/', f'{host}:{FTP_REMOTE_PATH}/'])


# ---------- Step 11: push html ----------

def step_11_push_html(args):
    host = require_samba_host()
    if not os.path.isdir(HTML_DIR):
        die(f"{HTML_DIR} does not exist. Run --stage-release first.")
    section(f"rsync html dir to {host}")
    filt = os.path.join(HTML_DIR, 'filt')
    rsync_with_confirm(['-aivOHP', '--chown=:rsync', '--del',
                        f'-f._{filt}',
                        f'{HTML_DIR}/', f'{host}:{HTML_REMOTE_PATH}/'])


# ---------- Step 12: print final release instructions ----------

def step_12_finish(args):
    state = load_state()
    version = state['version']
    v_ver = 'v' + version

    print(f"""\
{DASH_LINE}
The release PR has already updated master. Push the signed tag from this checkout:

    git push origin {v_ver}

If you have a 'samba' remote configured (git.samba.org:/data/git/rsync.git):

    git push samba {v_ver}

Then upload the tarball + .asc to the GitHub release for {v_ver},
and announce on rsync-announce@, rsync@, and Discord.

NOTE! Also update the PPAs if needed
""")


# ---------- shared rsync-with-confirm ----------

def rsync_with_confirm(rsync_args):
    """Run an rsync command in dry-run mode, then ask before running for real."""
    cmd_run(['rsync', '--dry-run', *rsync_args])
    if confirm("Run without --dry-run?"):
        cmd_run(['rsync', *rsync_args])


# ---------- phases ----------

def phase_prepare_pr(args):
    step_2_prepare(args)
    step_3_tweak(args)
    step_4_build(args)
    section("Release PR source prepared")
    print("Review the rsync-web release announcement in this PR before merging it.")


def phase_stage_release(args):
    step_4_build(args)
    step_5_verify(args)
    release_preflight()
    step_1_fetch(args)
    verify_artefact_inputs()
    step_6_tag(args)
    step_7_tarball(args)
    step_8_update_ftp(args)
    step_9_toplinks(args)
    state = load_state()
    state['staged_commit'] = cmd_txt_chk(['git', 'rev-parse', 'HEAD']).out.strip()
    save_state(state)


def phase_publish_release(args):
    state = load_state()
    dirty = cmd_txt_chk(['git', 'status', '--porcelain=v1']).out
    if dirty:
        die("Release publication requires a clean checkout:\n" + dirty)
    head = cmd_txt_chk(['git', 'rev-parse', 'HEAD']).out.strip()
    if state.get('staged_commit') != head:
        die("The current commit was not staged by --stage-release.")

    version = state['version']
    lastversion = state['lastversion']
    tag = 'v' + version
    target = cmd_txt_chk(['git', 'rev-list', '-n', '1', tag]).out.strip()
    if target != head:
        die(f"Tag {tag} does not point to staged commit {head}.")
    cmd_chk(['git', 'tag', '-v', tag])

    artefacts = [
        os.path.join(FTP_DIR, state['srcdir'], f'rsync-{version}.tar.gz'),
        os.path.join(FTP_DIR, state['srcdiffdir'],
                     f'rsync-{lastversion}-{version}.diffs.gz'),
    ]
    for fn in artefacts:
        if not os.path.isfile(fn) or not os.path.isfile(fn + '.asc'):
            die(f"Missing staged release artefact or signature: {fn}")
        cmd_chk(['gpg', '--verify', fn + '.asc', fn])

    verify_html_snapshot()

    step_10_push_ftp(args)
    step_11_push_html(args)
    step_12_finish(args)


PHASE_FUNCS = {
    'prepare-pr': phase_prepare_pr,
    'stage-release': phase_stage_release,
    'publish-release': phase_publish_release,
}


def signal_handler(sig, frame):
    die("\nAborting due to SIGINT.")


def main():
    parser = argparse.ArgumentParser(
        description="Phase-based release script for rsync.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run --list to see the release phases. Each invocation runs one phase.")
    parser.add_argument('--branch', '-b', dest='master_branch', default='master',
                        help="The branch to release (default: master).")
    parser.add_argument('--list', action='store_true',
                        help="List all release phases and exit.")
    grp = parser.add_mutually_exclusive_group()
    for flag, descr in PHASES:
        grp.add_argument('--' + flag, dest='phase', action='store_const',
                         const=flag, help=descr)
    args = parser.parse_args()

    if args.list:
        print("Release phases:")
        for flag, descr in PHASES:
            print(f"  --{flag:18s} {descr}")
        return

    if not args.phase:
        parser.error("pick one release phase (or --list to see them).")

    signal.signal(signal.SIGINT, signal_handler)
    os.environ['LESS'] = 'mqeiXR'
    PHASE_FUNCS[args.phase](args)


if __name__ == '__main__':
    main()

# vim: sw=4 et ft=python
