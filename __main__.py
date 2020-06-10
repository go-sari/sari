import os

import sys
from loguru import logger

from main.domain import log_issues
from main.gatherer import ModelBuilder
from main.updater import Updater


def in_automation():
    return os.environ.get("CI") == "true"


logger.remove()
logger.add(sys.stdout, colorize=(not in_automation()),
           format="<green>{time:HH:mm:ss.SSS}</green> {level} <lvl>{message}</lvl>")

model, issues = ModelBuilder().build()
log_issues(issues)
Updater(model).update_all()
