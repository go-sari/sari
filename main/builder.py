import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple

from prodict import Prodict

from main.aws_client import AwsClient
from main.aws_gatherer import AwsGatherer
from main.cfg_gatherer import UserConfigGatherer, DatabaseConfigGatherer
from main.dict import dict_deep_merge
from main.issue import Issue
from main.mysql_gatherer import MySqlGatherer
from main.okta_gatherer import OktaGatherer


def build_model(config: Prodict) -> Tuple[Prodict, List[Issue]]:
    model = Prodict(aws=config.aws, okta=config.okta, job={})

    # TODO: handle this using DI
    executor = ThreadPoolExecutor()
    aws = AwsClient(model.aws.region)
    aws_gatherer = AwsGatherer(aws)
    config_dir = config.system.config_dir
    database_config_gatherer = DatabaseConfigGatherer(config.master_password_defaults,
                                                      f"{config_dir}/{model.aws.region}/databases.yaml", aws)
    user_config_gatherer = UserConfigGatherer(f"{config_dir}/users.yaml")
    okta_api_token = os.environ["OKTA_API_TOKEN"]
    okta_gatherer = OktaGatherer(okta_api_token, executor)
    mysql_gatherer = MySqlGatherer(executor, config.system.proxy)

    all_issues: List[Issue] = []
    for gatherer in [
        # TODO: infer this order based on some explicit declaration like:
        #   1. Having 1 class for each gatherer
        #   2. This class reports 'requires' and 'produces'
        #   Example for AwsGatherer.gather_rds_info:
        #     requires() -> [aws.databases.*]
        #     produces() -> [aws.databases.*.endpoint]
        aws_gatherer.gather_account_info,
        database_config_gatherer.gather_rds_config,
        aws_gatherer.gather_rds_info,
        mysql_gatherer.gather_rds_status,
        user_config_gatherer.gather_user_config,
        okta_gatherer.gather_aws_app_info,
        okta_gatherer.gather_user_info,
    ]:
        # noinspection PyArgumentList
        delta, issues = gatherer(model)
        all_issues.extend(issues)
        dict_deep_merge(model, delta)

    return model, all_issues
