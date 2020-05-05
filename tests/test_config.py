from datetime import datetime
from unittest.mock import Mock

import pykwalify.core
from prodict import Prodict

import config.settings
from main import period
from main.aws_client import AwsClient
from main.cfg_gatherer import UserConfigGatherer, DatabaseConfigGatherer
from main.dict import dict_deep_merge


def test_yaml_config():

    settings = Prodict.from_dict(config.settings.settings)

    databases_yaml = f"./config/{settings.aws.region}/databases.yaml"
    yaml_validate(databases_yaml, "schema/databases.yaml")

    users_yaml = "./config/users.yaml"
    yaml_validate(users_yaml, "schema/users.yaml")

    aws = Mock(spec=AwsClient)
    aws.ssm_get_encrypted_parameter.return_value = ("a_master_password", (datetime.now()))

    model = Prodict(aws={"region": settings.aws.region})
    resp, issues = DatabaseConfigGatherer(databases_yaml, aws).gather_rds_config(model)
    assert not issues

    dict_deep_merge(model, resp)
    validator = period.Validator()
    resp, issues = UserConfigGatherer(users_yaml, validator).gather_user_config(model)
    assert not issues


def yaml_validate(data_file, schema_yaml):
    pykwalify.core.Core(
        source_file=data_file,
        schema_files=[schema_yaml]
    ).validate()
