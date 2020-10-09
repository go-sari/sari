import glob
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List
from typing import Tuple

import yaml
from prodict import Prodict

from main.aws_client import AwsClient
from main.domain import Issue
from main.util import dict_deep_merge
from .aws import AwsGatherer
from .config import DatabaseConfigGatherer, UserConfigGatherer, ServiceConfigGatherer, ApplicationConfigGatherer
from .dbinfo import DatabaseInfoGatherer
from .gatherer import Gatherer
from .mysql import MySqlGatherer
from .okta import OktaGatherer
from .pwd_resolver import MasterPasswordResolver


class ModelBuilder:
    def __init__(self):
        self.model = initial_model()
        self.issues = []

    def build(self) -> Tuple[Prodict, List[Issue]]:
        all_issues: List[Issue] = []

        self.apply_gatherer(CustomGatherer())
        gatherers = get_all_gatherers(self.model)
        for gatherer in gatherers:
            self.apply_gatherer(gatherer)

        return self.model, all_issues

    def apply_gatherer(self, gatherer: Gatherer):
        updates, issues = gatherer.gather(self.model)
        self.issues.extend(issues)
        dict_deep_merge(self.model, updates)


def initial_model() -> Prodict:
    config_dir = os.environ["SARI_CONFIG"]
    regions = discover_regions(config_dir)
    return Prodict(
        system={
            "config_dir": config_dir,
            "proxy": os.environ.get("PROXY"),
        },
        aws={
            "regions": regions,
            "single_region": regions[0] if len(regions) == 1 else None,
            "default_region": os.environ["AWS_REGION"],
            "iam_roles": {
                "trigger_run": os.environ["SARI_IAM_TRIGGER_ROLE_NAME"],
            },
        },
        okta={
            "organization": os.environ["OKTA_ORG_NAME"],
            "api_token": os.environ["OKTA_API_TOKEN"],
        },
        bastion_host={
            "hostname": os.environ["BH_HOSTNAME"],
            "port": os.environ.get("BH_PORT"),
            "admin_username": os.environ["BH_ADMIN_USERNAME"],
            "admin_private_key": os.environ.get("BH_ADMIN_PRIVATE_KEY"),
            "admin_key_filename": os.environ.get("BH_ADMIN_KEY_FILENAME"),
            "admin_key_passphrase": os.environ["BH_ADMIN_KEY_PASSPHRASE"],
            "proxy_username": os.environ["BH_PROXY_USERNAME"],
        },
        applications={},
        job={
            "next_transition": None,
        },
        grant_types={
            "query": ["SELECT"],
            "crud": ["SELECT", "UPDATE", "INSERT", "DELETE"],
        },
        master_password_defaults={
            r"([a-z][a-z0-9-]+)": r"ssm:\1.master_password",
        },
    )


def discover_regions(basedir: str) -> List[str]:
    """Find out all configured AWS regions by looking into all top-level directories that contains
    `databases.yaml` file."""
    regions = []
    for entry in glob.glob(f"{basedir}/*/databases.yaml"):
        *_, region, _ = entry.split("/")
        regions.append(region)
    return regions


def get_all_gatherers(model: Prodict) -> List[Gatherer]:
    config_dir = model.system.config_dir
    executor = ThreadPoolExecutor()
    gatherers: List[Gatherer] = [CustomGatherer(), AwsGatherer(AwsClient())]
    for region in model.aws.regions:
        aws_client = AwsClient(region)
        pwd_resolver = MasterPasswordResolver(aws_client, model.custom.master_password_defaults)
        gatherers.append(DatabaseConfigGatherer(region, f"{config_dir}/{region}/databases.yaml", pwd_resolver))
        gatherers.append(DatabaseInfoGatherer(aws_client, pwd_resolver))
    gatherers.append(MySqlGatherer(executor, model.system.proxy))
    gatherers.append(UserConfigGatherer(f"{config_dir}/users.yaml"))
    services_yaml = f"{config_dir}/services.yaml"
    if os.path.exists(services_yaml):
        gatherers.append(ServiceConfigGatherer(services_yaml))
    applications_yaml = f"{config_dir}/applications.yaml"
    if os.path.exists(applications_yaml):
        gatherers.append(ApplicationConfigGatherer(applications_yaml))
    okta_gatherer = OktaGatherer(model.okta.api_token, executor)
    gatherers.append(okta_gatherer)
    return gatherers


class CustomGatherer(Gatherer):
    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        """Loads the optional `custom.yaml` from the configuration directory."""
        issues = []
        custom_yaml = f"{model.system.config_dir}/custom.yaml"
        if os.path.exists(custom_yaml):
            with open(custom_yaml) as file:
                custom = yaml.safe_load(file)
                # TODO: validate
        else:
            custom = {}
        return Prodict(custom=custom), issues
