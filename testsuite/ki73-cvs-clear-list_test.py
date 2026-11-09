#!/usr/bin/env python3
"""KI-73 regression: a `.cvsignore` containing only `!` (the clear-list token)
must not abort rsync in -C (cvs-exclude) mode.

In exclude.c the CLEAR_LIST branch guarded its trailing-character check with
`rule->rflags & FILTRULE_NO_PREFIXES`, but NO_PREFIXES is a template-level flag
that is intentionally NOT inherited by individual rules (it is excluded from
FILTRULES_FROM_CONTAINER). The guard therefore always evaluated as "prefixes
allowed" for CVS rules and rsync hit exit_cleanup(RERR_SYNTAX) instead of
clearing the list. The fix checks `template->rflags` instead.

# Verifies: SW-REQ-078
"""

from rsyncfns import (
    FROMDIR, TODIR,
    assert_exists, makepath, rmtree, run_rsync,
)

src = FROMDIR
rmtree(src)
rmtree(TODIR)
makepath(src)

# Real files that must survive the transfer.
(src / 'keep1.txt').write_text('one\n')
(src / 'keep2.txt').write_text('two\n')

# A .cvsignore whose sole content is the clear-list token. Pre-fix this drove
# parse_rule_tok() into the RERR_SYNTAX abort path.
(src / '.cvsignore').write_text('!\n')

# check=True (the default) calls test_fail() -> exit 1 on a non-zero rsync
# exit, which covers the abort (RERR_SYNTAX) regression directly.
run_rsync('-a', '-C', f'{src}/', f'{TODIR}/')

assert_exists(TODIR / 'keep1.txt', label='clear-list .cvsignore kept keep1')
assert_exists(TODIR / 'keep2.txt', label='clear-list .cvsignore kept keep2')

print("ki73: cvs-exclude .cvsignore '!' clear-list token no longer aborts")
