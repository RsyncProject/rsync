import enum


class Exit(enum.IntEnum):
    PASS = 0
    FAIL = 1
    ERROR = 2
    SKIP = 77
    XFAIL = 78
