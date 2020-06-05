import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple

from prodict import Prodict

from main.aws_client import AwsClient
from main.dict import dict_deep_merge
from main.gatherer import *
from main.issue import Issue
from main.password_resolver import MasterPasswordResolver


def build_model(config: Prodict) -> Tuple[Prodict, List[Issue]]:
    gatherers = get_all_gatherers(config)
    model = Prodict(aws=config.aws, okta=config.okta, bastion_host=config.bastion_host, job={})
    all_issues: List[Issue] = []
    for gatherer in gatherers:
        delta, issues = gatherer.gather(model)
        all_issues.extend(issues)
        dict_deep_merge(model, delta)

    return model, all_issues


def get_all_gatherers(config: Prodict) -> List[Gatherer]:
    config_dir = config.system.config_dir
    executor = ThreadPoolExecutor()
    gatherers: List[Gatherer] = [AwsGatherer(AwsClient())]
    for region in config.aws.regions:
        aws_client = AwsClient(region)
        pwd_resolver = MasterPasswordResolver(aws_client, config.master_password_defaults)
        gatherers.append(DatabaseConfigGatherer(region, f"{config_dir}/{region}/databases.yaml", pwd_resolver))
        gatherers.append(DatabaseInfoGatherer(aws_client, pwd_resolver))
    gatherers.append(MySqlGatherer(executor, config.system.proxy))
    gatherers.append(UserConfigGatherer(f"{config_dir}/users.yaml"))
    services_yaml = f"{config_dir}/services.yaml"
    if os.path.exists(services_yaml):
        gatherers.append(ServiceConfigGatherer(services_yaml))
    okta_gatherer = OktaGatherer(config.okta.api_token, executor)
    gatherers.append(okta_gatherer)
    return gatherers
