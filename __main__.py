import sys
from loguru import logger

from main.builder import build_model
from main.issue import log_issues
from main.synthesizer import Synthesizer

logger.remove()
logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss.SSS}</green> {level} <lvl>{message}</lvl>")

model, issues = build_model()
log_issues(issues)
Synthesizer(model).synthesize_all()
