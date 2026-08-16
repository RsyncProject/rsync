#!/usr/bin/env python3
"""The support utility must start on the suite's supported Python."""

import os
import subprocess
import sys
from pathlib import Path


source_dir = Path(os.environ.get("srcdir", Path(__file__).resolve().parents[1]))
script = source_dir / "support" / "git-set-file-times"
proc = subprocess.run(
    [sys.executable, str(script), "--help"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

if proc.returncode != 0:
    sys.stderr.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.stderr.write(
        "git-set-file-times did not start with Python %s\n"
        % ".".join(str(value) for value in sys.version_info[:3])
    )
    raise SystemExit(1)

if "usage: git-set-file-times" not in proc.stdout:
    sys.stderr.write("git-set-file-times --help did not print its usage\n")
    raise SystemExit(1)
