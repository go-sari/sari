import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Callable

from prodict import Prodict

from main.aws_client import AwsClient
from main.aws_gatherer import AwsGatherer
from main.cfg_gatherer import UserConfigGatherer, DatabaseConfigGatherer, ServiceConfigGatherer
from main.dict import dict_deep_merge
from main.issue import Issue
from main.mysql_gatherer import MySqlGatherer
from main.okta_gatherer import OktaGatherer
from main.password_resolver import MasterPasswordResolver


def build_model(config: Prodict) -> Tuple[Prodict, List[Issue]]:
    model = Prodict(aws=config.aws, okta=config.okta, bastion_host=config.bastion_host, job={})
    config_dir = config.system.config_dir
    executor = ThreadPoolExecutor()

    gatherers: List[Callable[[Prodict], Tuple[Prodict, List[Issue]]]] = []

    gatherers.append(AwsGatherer(AwsClient()).gather_general_info)

    for region in config.aws.regions:
        aws = AwsClient(region)
        pwd_resolver = MasterPasswordResolver(aws, config.master_password_defaults)
        gatherers.append(
            DatabaseConfigGatherer(region,
                                   f"{config_dir}/{region}/databases.yaml",
                                   pwd_resolver).gather_rds_config)
        gatherers.append(AwsGatherer(aws).gather_rds_info)

    gatherers.append(MySqlGatherer(executor, config.system.proxy).gather_rds_status)

    gatherers.append(UserConfigGatherer(f"{config_dir}/users.yaml").gather_user_config)

    services_yaml = f"{config_dir}/services.yaml"
    if os.path.exists(services_yaml):
        gatherers.append(ServiceConfigGatherer(services_yaml).gather_service_config)
    okta_gatherer = OktaGatherer(config.okta.api_token, executor)
    gatherers.append(okta_gatherer.gather_user_info)

    all_issues: List[Issue] = []
    for gatherer in gatherers:
        delta, issues = gatherer(model)
        all_issues.extend(issues)
        dict_deep_merge(model, delta)

    return model, all_issues
