#!/usr/bin/env python3

import os

from fixtures.devices import check_devices
from harness import metadata
from harness.results import test_skipped

metadata(features={'device_nodes'}, transports={'pipe'})

if os.geteuid() != 0:
    test_skipped('device tests need root', capability='device_nodes')

def make_special(path, kind, major=0, minor=0):
    try:
        if kind == 'p':
            os.mkfifo(path)
        else:
            mode = 0o644 | (0o020000 if kind == 'c' else 0o060000)
            os.mknod(path, mode, os.makedev(major, minor))
        return True
    except OSError:
        return False

check_devices(make_special)
