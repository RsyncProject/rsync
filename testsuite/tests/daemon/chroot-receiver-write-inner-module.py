#!/usr/bin/env python3

from harness.daemon_config import setup_chroot_inner
from harness.process import capture_command
from harness.rsync import rsync_argv, test_fail

base, inner, outside, src, url = setup_chroot_inner('chroot-write-inner')
(src / 'pwn').write_text('payload\n')
proc, out = capture_command(rsync_argv('-a', str(src / 'pwn'), f'{url}mod/linkparent/pwn'))
if (outside / 'pwn').exists():
    test_fail(f"receiver write escaped inner module through symlinked parent:\n{out}")
print("chroot-receiver-write-inner-module: symlinked parent did not publish outside inner module")
