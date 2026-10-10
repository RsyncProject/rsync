#!/usr/bin/env python3

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from harness.rsync import SCRATCHDIR, rmtree, test_fail, test_skipped

def makefile_vars(repo, names):
    found = {}
    makefile = repo / "Makefile"
    if makefile.exists():
        for line in makefile.read_text(errors="replace").splitlines():
            for name in names:
                if line.startswith(name + "="):
                    found[name] = line[len(name) + 1:].strip()
    return found

repo = Path(os.environ.get('RSYNC_SOURCE_UNDER_TEST', os.environ['srcdir']))
source_path = repo / "authenticate.c"
config_path = repo / "config.h"
if not source_path.exists() or not config_path.exists():
    test_skipped(f"configured rsync source tree unavailable at {repo}")

build_vars = makefile_vars(repo, ("CC", "CPPFLAGS"))

def compiler():
    value = os.environ.get("CC") or build_vars.get("CC")
    if value:
        return shlex.split(value)
    for name in ("cc", "clang", "gcc"):
        path = shutil.which(name)
        if path:
            return [path]
    return None

cc = compiler()
if not cc:
    test_skipped("no C compiler available for the O_CLOEXEC feature probe")

base = SCRATCHDIR / 'authenticate-no-ocloexec-build'
rmtree(base)
base.mkdir(parents=True)

source = source_path.read_text()
needle = '#include "rsync.h"\n'
if source.count(needle) != 1:
    test_fail(f"cannot locate feature-injection point in {source_path}")
source = source.replace(needle, needle + "#undef O_CLOEXEC\n", 1)
probe_c = base / "authenticate-no-ocloexec.c"
probe_o = base / "authenticate-no-ocloexec.o"
probe_c.write_text(source)

includes = [f"-I{repo}", f"-I{repo / 'popt'}", f"-I{repo / 'zlib'}",
            "-DHAVE_CONFIG_H", "-O0", "-g", "-Wall", "-Wextra"]
includes += shlex.split(build_vars.get("CPPFLAGS", ""))
build = subprocess.run(
    cc + includes + ["-c", str(probe_c), "-o", str(probe_o)],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
if build.returncode != 0:
    test_fail("authenticate.c does not compile without O_CLOEXEC:\n" + build.stdout)

print("authenticate.c compiles without O_CLOEXEC")
