#!/usr/bin/env python3

import os
import subprocess
import sys

from harness import metadata
from harness.environment import SCRATCHDIR
from harness.filesystem import assert_same, makepath
from harness.mutation import find_compiler
from harness.results import test_fail, test_skipped
from harness.rsync import rsync_argv

metadata(features={'linux_interpose'}, transports={'pipe'}, mutates={'filesystem', 'process'},
         tags={'path', 'regression'})

SHIM = r'''
#define _GNU_SOURCE 1
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int (*next_openat)(int, const char *, int, ...);
static int replaced;

__attribute__((constructor))
static void mark_loaded(void)
{
    const char *path = getenv("RSYNC_LEAF_LOAD_MARK");
    int fd;

    if (!path)
        return;
    fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd >= 0) {
        (void)write(fd, "loaded\n", 7);
        (void)close(fd);
    }
}

int openat(int dfd, const char *name, int flags, ...)
{
    mode_t mode = 0;
    va_list args;
    const char *parent = getenv("RSYNC_LEAF_PARENT");
    const char *directory = getenv("RSYNC_LEAF_DIRECTORY");
    const char *mark = getenv("RSYNC_LEAF_SWAP_MARK");

    if (flags & O_CREAT
#ifdef O_TMPFILE
        || (flags & O_TMPFILE) == O_TMPFILE
#endif
    ) {
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
    }
    if (!next_openat) {
        next_openat = dlsym(RTLD_NEXT, "openat");
        if (!next_openat) {
            errno = ENOSYS;
            return -1;
        }
    }
    if (!replaced && parent && directory && mark && name
        && strcmp(name, "data") == 0 && (flags & O_NOFOLLOW)) {
        char procpath[64], resolved[PATH_MAX];
        ssize_t length;

        snprintf(procpath, sizeof procpath, "/proc/self/fd/%d", dfd);
        length = readlink(procpath, resolved, sizeof resolved - 1);
        if (length >= 0) {
            resolved[length] = '\0';
            if (strcmp(resolved, parent) == 0) {
                int fd;
                replaced = 1;
                fd = open(mark, O_WRONLY | O_CREAT | O_TRUNC, 0600);
                if (fd >= 0) {
                    (void)write(fd, "replaced\n", 9);
                    (void)close(fd);
                }
                return next_openat(AT_FDCWD, directory, flags | O_DIRECTORY, mode);
            }
        }
    }
    return next_openat(dfd, name, flags, mode);
}
'''

if not sys.platform.startswith('linux'):
    test_skipped('source-leaf replacement requires Linux LD_PRELOAD and /proc',
                 capability='linux_interpose')
compiler = find_compiler()
if not compiler:
    test_skipped('source-leaf replacement requires a C compiler',
                 capability='linux_interpose')

source = SCRATCHDIR / 'source'
alternate = SCRATCHDIR / 'directory'
makepath(source, alternate)
(source / 'data').write_text('original file\n')
(source / 'later').write_text('later file\n')

cfile = SCRATCHDIR / 'replace_source_leaf.c'
library = SCRATCHDIR / 'replace_source_leaf.so'
cfile.write_text(SHIM)
build = subprocess.run(compiler + ['-shared', '-fPIC', '-O2', '-Wall', '-Wextra',
                                   '-o', str(library), str(cfile), '-ldl'],
                       capture_output=True, text=True)
if build.returncode:
    test_fail(f'could not build source-leaf replacement shim: {build.stderr}')

for name, mode in (('readable', 0o755), ('search-only', 0o111)):
    os.chmod(alternate, mode)
    destination = SCRATCHDIR / name
    loaded = SCRATCHDIR / f'{name}_shim_loaded'
    replaced = SCRATCHDIR / f'{name}_leaf_replaced'
    environment = os.environ.copy()
    environment.update(LD_PRELOAD=str(library), RSYNC_LEAF_PARENT=str(source),
                       RSYNC_LEAF_DIRECTORY=str(alternate), RSYNC_LEAF_LOAD_MARK=str(loaded),
                       RSYNC_LEAF_SWAP_MARK=str(replaced))
    result = subprocess.run(rsync_argv('-a', f'{source}/', f'{destination}/'),
                            capture_output=True, text=True, env=environment, timeout=30)
    if not loaded.exists():
        test_skipped('source-leaf replacement shim was not loaded', capability='linux_interpose')
    if not replaced.exists():
        test_fail('source-leaf replacement shim did not intercept the sender file open')
    if result.returncode != 23 or 'send_files failed to open' not in result.stderr:
        test_fail(f'{name} directory replacement did not produce an open refusal: {result!r}')
    if (destination / 'data').exists():
        test_fail(f'{name} directory replacement created an output file')
    assert_same(source / 'later', destination / 'later', label='subsequent file')
os.chmod(alternate, 0o755)
