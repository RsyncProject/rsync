import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from .filesystem import make_data_file, rmtree
from .process import rsync_argv
from .results import test_fail

TARGET = 'zzz_target'

_RACE_TIMEOUT_SET = 'race_timeout' in os.environ
try:
    RACE_TIMEOUT = float(os.environ.get('race_timeout', '5'))
except ValueError:
    RACE_TIMEOUT = 5.0
    _RACE_TIMEOUT_SET = False

def race_budget(default: float = 5.0) -> float:
    if _RACE_TIMEOUT_SET and math.isfinite(RACE_TIMEOUT) and RACE_TIMEOUT > 0:
        return RACE_TIMEOUT
    return default

def start_path_flipper(name_a, name_b):
    code = (
        "import os, sys, time\n"
        "a, b = sys.argv[1], sys.argv[2]\n"
        "tmp = a + '.flip'\n"
        "parent = os.getppid()\n"
        "deadline = time.monotonic() + 300\n"
        "while os.getppid() == parent and time.monotonic() < deadline:\n"
        "    try:\n"
        "        os.rename(a, tmp); os.rename(b, a); os.rename(tmp, b)\n"
        "    except OSError:\n"
        "        pass\n"
    )
    return subprocess.Popen([sys.executable, '-c', code, str(name_a), str(name_b)])

def stop_flipper(process):
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

_C_FLIPPER_SRC = r'''
/* Repeatedly swap sibling paths while rsync traverses them. Prefer atomic
 * renameat2(RENAME_EXCHANGE) and fall back to three renames. */
#define _GNU_SOURCE 1
#include "config.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <sys/stat.h>
#if defined(__linux__)
# include <sys/syscall.h>
# ifndef RENAME_EXCHANGE
#  define RENAME_EXCHANGE (1 << 1)
# endif
#endif

static double mono(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int use_exchange = 1;

static void flip(const char *a, const char *b) {
#if defined(__linux__) && defined(SYS_renameat2)
    if (use_exchange) {
        if (syscall(SYS_renameat2, AT_FDCWD, a, AT_FDCWD, b, RENAME_EXCHANGE) == 0)
            return;
        if (errno == ENOSYS || errno == EINVAL || errno == EOPNOTSUPP)
            use_exchange = 0;
        else
            return;
    }
#endif
    {
        char tmp[4096];
        if (snprintf(tmp, sizeof tmp, "%s.flip", a) >= (int)sizeof tmp)
            return;
        rmdir(tmp); unlink(tmp);
        if (rename(a, tmp) != 0) {
            mkdir(a, 0700);
            return;
        }
        if (rename(b, a) != 0)
            rename(tmp, a);
        else
            rename(tmp, b);
    }
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s PATH_A PATH_B\n", argv[0]);
        return 2;
    }
    const char *a = argv[1], *b = argv[2];
    pid_t parent = getppid();
    double deadline = mono() + 300.0;
    while (getppid() == parent && mono() < deadline)
        flip(a, b);
    return 0;
}
'''

_c_flipper_bin = None

def _detect_cc():
    compiler = os.environ.get('CC')
    if compiler:
        return compiler
    for directory in (Path(os.environ['TOOLDIR']), Path(os.environ['srcdir'])):
        makefile = directory / 'Makefile'
        if not makefile.is_file():
            continue
        match = re.search(r'(?m)^CC\s*=\s*(.+?)\s*$', makefile.read_text())
        if match and match.group(1):
            return match.group(1)
    for compiler in ('cc', 'gcc', 'clang'):
        if shutil.which(compiler):
            return compiler
    return None

def compile_c_flipper():
    global _c_flipper_bin
    if _c_flipper_bin is not None:
        return _c_flipper_bin or None
    compiler = _detect_cc()
    scratch = Path(os.environ['scratchdir'])
    source_root = Path(os.environ['srcdir'])
    tools = Path(os.environ['TOOLDIR'])
    source = scratch / 't_flipper.c'
    output = scratch / ('t_flipper' + ('.exe' if os.name == 'nt' else ''))
    if not compiler:
        _c_flipper_bin = ''
        return None
    source.write_text(_C_FLIPPER_SRC)
    command = shlex.split(compiler) + [
        '-O2', f'-I{tools}', f'-I{source_root}', '-o', str(output), str(source),
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    if result.returncode != 0 or not os.access(output, os.X_OK):
        _c_flipper_bin = ''
        return None
    _c_flipper_bin = str(output)
    return _c_flipper_bin

def start_c_flipper(name_a, name_b):
    executable = compile_c_flipper()
    if executable:
        return subprocess.Popen([executable, str(name_a), str(name_b)])
    return start_path_flipper(name_a, name_b)

def find_attacker_uid():
    import pwd
    for name in ('nobody', 'nfsnobody', 'daemon'):
        try:
            uid = pwd.getpwnam(name).pw_uid
        except KeyError:
            continue
        if uid != 0 and uid != os.geteuid():
            return uid
    return None

def run_symlink_matrix(option, case, *, paths=('abs', 'rel'),
                       locations=('leaf', 'parent'), label=''):
    title = option + (f' [{label}]' if label else '')
    attacker_uid = find_attacker_uid() if os.geteuid() == 0 else None
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    scratch = Path(os.environ['scratchdir'])

    for path_form in paths:
        for location in locations:
            for insecure in (False, True):
                owners = ('self', 'cross') if attacker_uid is not None else ('self',)
                for owner in owners:
                    base = scratch / (f'{slug}-{owner}-{path_form}-{location}-'
                                      + ('ins' if insecure else 'safe'))
                    rmtree(base)
                    base.mkdir(parents=True)
                    context = SimpleNamespace(
                        base=base, outside=base / 'outside', plant=base / 'plant',
                        owner=owner, att_uid=attacker_uid, abspath=path_form,
                        where=location, insecure=insecure,
                    )
                    context.outside.mkdir()
                    context.plant.mkdir()

                    def plant_link(path, target, current=context):
                        os.symlink(target, path)
                        if current.owner == 'cross':
                            os.lchown(path, current.att_uid, current.att_uid)

                    context.plant_link = plant_link
                    followed = bool(case(context))
                    expected = insecure or owner == 'self'
                    cell = f"{path_form} {location} {'insecure' if insecure else 'safe'}"
                    if followed and not expected:
                        test_fail(
                            f'{title}: CROSS-UID {cell}: the planted symlink was '
                            'FOLLOWED (operation escaped outside). An operator path '
                            'must refuse a symlink not owned by uid 0 or the effective uid.')
                    if not followed and expected:
                        reason = ('--insecure-links did not restore symlink following'
                                  if insecure else
                                  'the operator-owned symlink was refused')
                        prefix = 'CROSS' if owner == 'cross' else 'SAME'
                        test_fail(f'{title}: {prefix}-UID {cell}: {reason}')
    if attacker_uid is None and os.geteuid() != 0:
        print(f'{title}: same-uid cells confirmed; cross-uid cells need root (skipped)')

def plant_operator_symlink(context, relative_anchor, kind='dir'):
    base = context.plant if context.abspath == 'abs' else relative_anchor
    if kind == 'file':
        victim = context.outside / 'victim'
        if context.where == 'leaf':
            link = base / 'osl'
            context.plant_link(link, victim)
            return (str(link) if context.abspath == 'abs' else 'osl'), victim
        link = base / 'opd'
        context.plant_link(link, context.outside)
        return ((str(link / 'victim') if context.abspath == 'abs' else 'opd/victim'), victim)
    if context.where == 'leaf':
        link = base / 'osl'
        context.plant_link(link, context.outside)
        return (str(link) if context.abspath == 'abs' else 'osl'), context.outside
    link = base / 'opd'
    context.plant_link(link, context.outside)
    return ((str(link / 'sub') if context.abspath == 'abs' else 'opd/sub'),
            context.outside / 'sub')

def run_mutating_transfer(setup, mutate, *, options=('-a',),
                          pacer_bytes=6 * 1024 * 1024, bwlimit=1500, delay=0.6):
    base = Path(os.environ['scratchdir']) / 'mutate'
    rmtree(base)
    source = base / 'source'
    destination = base / 'destination'
    source.mkdir(parents=True)
    destination.mkdir()
    make_data_file(source / 'aaa_pacer', pacer_bytes)
    setup(source)

    errors = []

    def worker():
        time.sleep(delay)
        try:
            mutate(source)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    result = subprocess.run(
        rsync_argv(os.environ['RSYNC'], *options, '--no-inc-recursive',
                   f'--bwlimit={bwlimit}', f'{source}/', f'{destination}/'),
        capture_output=True, text=True,
    )
    thread.join()
    if errors:
        test_fail(f'test mutation failed: {errors[0]!r}')
    return result, source, destination

def assert_no_protocol_abort(result):
    output = result.stdout + result.stderr
    if 'received more data than file length' in output:
        test_fail(f'transfer aborted after the source changed:\n{output}')
    if result.returncode == 2:
        test_fail(f'transfer ended with a protocol error:\n{output}')
    if result.returncode not in (0, 23, 24):
        test_fail(f'unexpected rsync exit {result.returncode}:\n{output}')
