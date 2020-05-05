from collections import namedtuple
from enum import IntEnum
from typing import List

from loguru import logger

Issue = namedtuple('Issue', ('level', 'type', 'id', 'message'))


class IssueLevel(IntEnum):
    """Names must match the names used by loguru"""
    WARNING = 1
    ERROR = 2
    CRITICAL = 3
    # TODO: create match()


# Eventually redirects all WARN+ log messages to Issues HTML
def log_issues(issues: List[Issue]):
    """
    Log all issues using loguru.logger using the respective level.
    """
    if issues:
        logger.info("Issues:")
    for issue in issues:
        logger.log(issue.level.name, f"  {issue.type}={issue.id} :: {issue.message}")
