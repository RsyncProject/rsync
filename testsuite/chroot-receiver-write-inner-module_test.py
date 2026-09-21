#!/usr/bin/env python3
from rsyncfns import run_checked, setup_chroot_inner
from rsyncfns import rsync_argv, test_fail

base, inner, outside, src, url = setup_chroot_inner('chroot-write-inner')
(src / 'pwn').write_text('payload\n')
proc, out = run_checked(rsync_argv('-a', str(src / 'pwn'), f'{url}mod/linkparent/pwn'))
if (outside / 'pwn').exists():
    test_fail(f"receiver write escaped inner module through symlinked parent:\n{out}")
print("chroot-receiver-write-inner-module: symlinked parent did not publish outside inner module")
