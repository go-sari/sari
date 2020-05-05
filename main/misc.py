import fnmatch
import re
from typing import List

_WILDCARD_CHECK = re.compile('([*?[])')


def wc_expand(name: str, names: List[str]) -> List[str]:
    if _has_magic(name):
        return fnmatch.filter(names, name)
    return [name] if name in names else []


def _has_magic(s):
    return _WILDCARD_CHECK.search(s) is not None
