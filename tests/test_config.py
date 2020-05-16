from datetime import datetime
from unittest.mock import Mock

import pykwalify.core
from prodict import Prodict

from main.aws_client import AwsClient
from main.cfg_gatherer import UserConfigGatherer, DatabaseConfigGatherer
from main.config import load_config
from main.dict import dict_deep_merge


# TODO: it doesn't make sense to validate the example configuration under the ./config directory.
#  This validation is useful for the configuration project used as, for example, inside an GitHub Action.
def test_yaml_config():

    config = load_config()

    config_dir = config.system.config_dir
    databases_yaml = f"{config_dir}/{config.aws.region}/databases.yaml"
    yaml_validate(databases_yaml, "schema/databases.yaml")

    users_yaml = f"{config_dir}/users.yaml"
    yaml_validate(users_yaml, "schema/users.yaml")

    aws = Mock(spec=AwsClient)
    aws.ssm_get_encrypted_parameter.return_value = ("a_master_password", (datetime.now()))

    model = Prodict(system={}, aws={"region": config.aws.region})
    resp, issues = DatabaseConfigGatherer(config.master_password_defaults, databases_yaml, aws).gather_rds_config(model)
    assert not issues

    dict_deep_merge(model, resp)
    resp, issues = UserConfigGatherer(users_yaml).gather_user_config(model)
    assert not issues


def yaml_validate(data_file, schema_yaml):
    pykwalify.core.Core(
        source_file=data_file,
        schema_files=[schema_yaml]
    ).validate()
