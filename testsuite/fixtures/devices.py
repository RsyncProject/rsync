import os
import sys

from harness.rsync import (CHKDIR, FROMDIR, TODIR, all_plus, allspace, dots, checkdiff,
                           rsync_ls_lR, run_rsync, test_fail, test_skipped, v_filt)

def check_devices(make_special):
    vv = run_rsync('-VV', check=True, capture_output=True)
    can_hlink_special = '"hardlink_specials": true' in vv.stdout

    FROMDIR.mkdir(parents=True, exist_ok=True)
    TODIR.mkdir(parents=True, exist_ok=True)

    if not make_special(FROMDIR / 'char', 'c', 41, 67):
        test_skipped("Can't create char device node", capability='device_nodes')
    if not make_special(FROMDIR / 'char2', 'c', 42, 68):
        test_skipped("Can't create char device node", capability='device_nodes')
    if not make_special(FROMDIR / 'char3', 'c', 42, 69):
        test_skipped("Can't create char device node", capability='device_nodes')
    if not make_special(FROMDIR / 'block', 'b', 42, 69):
        test_skipped("Can't create block device node", capability='device_nodes')
    if not make_special(FROMDIR / 'block2', 'b', 42, 73):
        test_skipped("Can't create block device node", capability='device_nodes')
    if not make_special(FROMDIR / 'block3', 'b', 105, 73):
        test_skipped("Can't create block device node", capability='device_nodes')

    if can_hlink_special:
        try:
            os.link(FROMDIR / 'block3', FROMDIR / 'block3.5')
        except OSError:
            print("Skipping hard-linked device test... (link failed)")
            can_hlink_special = False
    else:
        print("Skipping hard-linked device test...")

    if not make_special(FROMDIR / 'fifo', 'p'):
        test_skipped("Can't run mkfifo", capability='device_nodes')

    ref = (FROMDIR / 'block').stat()
    os.utime(FROMDIR / 'block', (ref.st_atime, ref.st_mtime), follow_symlinks=False)
    os.utime(FROMDIR / 'block2', (ref.st_atime, ref.st_mtime), follow_symlinks=False)

    checkdiff(['-ai', f'{FROMDIR}/block', f'{TODIR}/block2'],
              f"cD{all_plus} block\n")

    checkdiff(['-ai', f'{FROMDIR}/block2', f'{TODIR}/block'],
              f"cD{all_plus} block2\n")

    ref = (TODIR / 'block').stat()
    os.utime(FROMDIR / 'block3', (ref.st_atime, ref.st_mtime + 10),
             follow_symlinks=False)

    checkdiff(['-Di', f'{FROMDIR}/block3', f'{TODIR}/block'],
              f"cDc.T.{dots} block3\n")

    for source, destination in ((FROMDIR / 'block', TODIR / 'block'),
                                (FROMDIR, TODIR)):
        st = source.stat()
        os.utime(destination, (st.st_atime, st.st_mtime + 10),
                 follow_symlinks=False)

    chkfile_lines = [
        f".d..t.{dots} ./",
        f"cDc.t.{dots} block",
        f"cDc...{dots} block2",
        f"cD{all_plus} block3",
    ]
    if can_hlink_special:
        chkfile_lines.append(f"hD{all_plus} block3.5 => block3")
    chkfile_lines += [
        f"cD{all_plus} char",
        f"cD{all_plus} char2",
        f"cD{all_plus} char3",
        f"cS{all_plus} fifo",
    ]
    expected = '\n'.join(chkfile_lines) + '\n'

    checkdiff(['-aiHvv', f'{FROMDIR}/', f'{TODIR}/'], expected, filter=v_filt)

    print("check how the directory listings compare with diff:\n")
    ls_from = rsync_ls_lR(FROMDIR)
    ls_to = rsync_ls_lR(TODIR)
    if ls_from != ls_to:
        from difflib import unified_diff
        sys.stdout.write(''.join(unified_diff(
            ls_from.splitlines(keepends=True),
            ls_to.splitlines(keepends=True),
            fromfile='from', tofile='to',
        )))
        test_fail("from/to listings differ after device transfer")

    if can_hlink_special:
        expected = (
            f"created directory {CHKDIR}\n"
            f"cd{allspace} ./\n"
            f"hD{allspace} block\n"
            f"hD{allspace} block2\n"
            f"hD{allspace} block3\n"
            f"hD{allspace} block3.5\n"
            f"hD{allspace} char\n"
            f"hD{allspace} char2\n"
            f"hD{allspace} char3\n"
            f"hS{allspace} fifo\n"
        )
        checkdiff(['-aii', f'--link-dest={TODIR}',
                   f'{FROMDIR}/', f'{CHKDIR}/'], expected)
