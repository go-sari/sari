#!/usr/bin/env python3

import argparse
import json
import os
import sys
from pathlib import Path
from subprocess import Popen, PIPE

from loguru import logger

from main.aws_client import AwsClient
from main.domain import log_issues
from main.gatherer import ModelBuilder
from main.util import purge_pulumi_stack


def main():
    logger.remove()
    logger.add(sys.stdout, colorize=(not in_automation()),
               format="<green>{time:HH:mm:ss.SSS}</green> {level} <lvl>{message}</lvl>")

    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True,
                        help='Output file to contain the resulting model.')
    parser.add_argument('--purge-pulumi-stack', action='store_true',
                        help='Purge zombie resources from Pulumi Stack.')
    args = parser.parse_args()

    model, issues = ModelBuilder().build()
    log_issues(issues)

    model_json = json.dumps(model)
    # TODO: avoid persisting passwords in plain.
    #  How: assuming only SSM-stored passwords are supported, postpone dereferencing them to the next stage.
    Path(args.model).write_text(model_json)

    if args.purge_pulumi_stack:
        do_purge_pulumi_stack()


def do_purge_pulumi_stack():
    with Popen(["pulumi", "--non-interactive", "stack", "export"], stdout=PIPE) as proc:
        original_stack = json.loads(proc.stdout.read())
    live_rds_endpoints = AwsClient.get_rds_known_endpoints()
    updated_stack, num_changes = purge_pulumi_stack(original_stack, live_rds_endpoints)
    if num_changes > 0:
        logger.info(f"Purging {num_changes} resources from Pulumi Stack")
        with Popen(["pulumi", "--non-interactive", "stack", "import"], stdin=PIPE) as proc:
            proc.stdin.write(json.dumps(updated_stack).encode())


def in_automation():
    return os.environ.get("CI") == "true"


if __name__ == "__main__":
    main()
