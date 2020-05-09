import os

import yaml
from prodict import Prodict


def load_config() -> Prodict:
    config_dir = os.environ.get("CONFIG", "./config")
    with open(f"{config_dir}/config.yaml") as file:
        config = yaml.safe_load(file)

    proxy = os.environ.get("PROXY")
    config["system"] = {
        "config_dir": config_dir,
        "proxy": proxy
    }
    return Prodict.from_dict(config)
