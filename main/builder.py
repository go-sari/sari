import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple

import pulumi
# noinspection PyPackageRequirements
import socks
from prodict import Prodict

from config.settings import settings
from main import period
from main.aws_client import AwsClient
from main.aws_gatherer import AwsGatherer
from main.cfg_gatherer import UserConfigGatherer, DatabaseConfigGatherer
from main.dict import dict_deep_merge
from main.issue import Issue
from main.mysql_gatherer import MySqlGatherer
from main.okta_gatherer import OktaGatherer


def build_model() -> Tuple[Prodict, List[Issue]]:
    config = pulumi.Config()
    proxy = config.get("socks5_proxy")

    socks5_proxy = _endpoint_split(proxy, socks.DEFAULT_PORTS[socks.SOCKS5]) \
        if proxy else None

    model = Prodict.from_dict(settings)

    model.update(Prodict(config={
        "socks_proxy_url": f"socks5://{socks5_proxy[0]}:{socks5_proxy[1]}" if socks5_proxy else "",
    }))

    # TODO: handle this using DI
    executor = ThreadPoolExecutor()
    aws = AwsClient(model.aws.region)
    aws_gatherer = AwsGatherer(aws)
    validator = period.TrackingValidator()
    database_config_gatherer = DatabaseConfigGatherer(f"./config/{model.aws.region}/databases.yaml", aws)
    user_config_gatherer = UserConfigGatherer(f"./config/users.yaml", validator)
    okta_api_token = os.environ["OKTA_API_TOKEN"]
    okta_gatherer = OktaGatherer(okta_api_token, executor)
    mysql_gatherer = MySqlGatherer(executor, socks5_proxy)

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


def _endpoint_split(endpoint, default_port):
    parts = endpoint.split(':')
    return parts[0], int(parts[1]) if parts[1:] else default_port
