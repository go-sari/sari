import os

import sys
from loguru import logger

from main.builder import build_model
from main.config import load_config
from main.issue import log_issues
from main.synthesizer import Synthesizer


def in_ci():
    return os.environ.get("CODEBUILD_CI", None) == "true"


logger.remove()
if in_ci():
    logger.add(sys.stdout, colorize=False, format="{time:HH:mm:ss.SSS} {level} {message}")
else:
    logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss.SSS}</green> {level} <lvl>{message}</lvl>")

config = load_config()
model, issues = build_model(config)
log_issues(issues)
Synthesizer(config, model).synthesize_all()
