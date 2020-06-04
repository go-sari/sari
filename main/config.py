import glob
import os

import yaml
from prodict import Prodict
from typing import List


def load_config() -> Prodict:
    config_dir = os.environ.get("CONFIG", "./config")
    with open(f"{config_dir}/config.yaml") as file:
        config = yaml.safe_load(file)

    regions = _discover_regions(config_dir)
    config.update({
        "system": {
            "config_dir": config_dir,
            "proxy": (os.environ.get("PROXY"))
        },
        "aws": {
            "regions": regions,
            "single_region": regions[0] if len(regions) == 1 else None,
            "default_region": os.environ["AWS_REGION"],
        },
        "okta": {
            "organization": os.environ["OKTA_ORG_NAME"],
            "api_token": os.environ["OKTA_API_TOKEN"],
            "aws_app": {
                "app_id": os.environ["OKTA_AWS_APP_ID"],
                "iam_idp": os.environ["OKTA_AWS_APP_IAM_IDP"],
            }
        },
        "bastion_host": {
            "hostname": os.environ["BH_HOSTNAME"],
            "port": os.environ.get("BH_PORT"),
            "admin_username": os.environ["BH_ADMIN_USERNAME"],
            "admin_private_key": os.environ.get("BH_ADMIN_PRIVATE_KEY"),
            "admin_key_filename": os.environ.get("BH_ADMIN_KEY_FILENAME"),
            "admin_key_passphrase": os.environ["BH_ADMIN_KEY_PASSPHRASE"],
            "proxy_username": os.environ["BH_PROXY_USERNAME"],
        }
    })
    return Prodict.from_dict(config)


def _discover_regions(basedir: str) -> List[str]:
    regions = []
    for entry in glob.glob(f"{basedir}/*/databases.yaml"):
        *_, region, _ = entry.split("/")
        regions.append(region)
    return regions
