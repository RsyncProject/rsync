"""Exit codes a test reports to runtests.py (autotools test convention).

Shared by runtests.py (the harness, which reads these from each test) and
rsyncfns.py (the helpers, which exit with them) so the 0/1/2/77/78 values are
named in exactly one place.  This module has no import-time side effects, so
runtests.py can import it without pulling in rsyncfns's environment checks.
"""

import enum


class Exit(enum.IntEnum):
    PASS = 0
    FAIL = 1
    ERROR = 2     # the test could not run (e.g. missing environment)
    SKIP = 77
    XFAIL = 78    # expected failure: a known, documented residual
