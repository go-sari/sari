from enum import IntEnum


class DbStatus(IntEnum):
    ABSENT = 0
    DISABLED = 1
    ENABLED = 2
    AUTO_ENABLED = 2
    ACCESSIBLE = 3
