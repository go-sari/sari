import random
import re
from concurrent.futures.thread import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat
from urllib.parse import unquote

import boto3
import pytz
from dictdiffer import diff
from httmock import HTTMock, response, urlmatch
from moto import mock_rds2, mock_ssm, mock_sts, mock_ec2
from moto.ec2.utils import random_security_group_id
from moto.iam.models import ACCOUNT_ID
from prodict import Prodict

from main.aws_client import AwsClient
from main.aws_gatherer import AwsGatherer
from main.cfg_gatherer import DatabaseConfigGatherer, UserConfigGatherer, ServiceConfigGatherer
from main.dict import dict_deep_merge
from main.issue import IssueLevel
from main.okta_gatherer import OktaGatherer

AWS_REGION = "us-west-2"

# The constants below are valid for moto v1.3.14
MOTO_RDS_FIXED_SUBDOMAIN = "aaaaaaaaaa"
MOTO_RDS_FIXED_RESOURCE_ID = "db-M5ENSHXFPU6XHZ4G4ZEI5QIO2U"

RDS_CONFIG_DATABASES = {
    "blackwells": {
        "status": "ENABLED",
        "ssm_master_password": "blackwells.master_password",
        "plain_master_password": "focused_mendel",
        "password_age": False,
        "permissions": {},
    },
    "foyles": {
        "status": "DISABLED",
    },
    "whsmith": {
        "status": "ENABLED",
        "ssm_master_password": "whsmith.master_password",
        "plain_master_password": "quirky_ganguly",
        "password_age": False,
        "permissions": {},
    },
}

RDS_INFO_DATABASES = {
    "blackwells": {
        "db_name": "db_blackwells",
        "master_username": "acme",
        "dbi_resource_id": MOTO_RDS_FIXED_RESOURCE_ID,
        "endpoint": {
            "address": f"blackwells.{MOTO_RDS_FIXED_SUBDOMAIN}.{AWS_REGION}.rds.amazonaws.com",
            "port": 3306,
        },
        "availability_zone": f"{AWS_REGION}a",
        "vpc_security_group_ids": ["sg-93ad699f"],
        "primary_subnet": "subnet-283fefc6",
    },
    "whsmith": {
        "status": "ABSENT",
    },
}

USERS_CONFIG = {
    "leroy.trent@acme.com": {
        "db_username": "leroy.trent@acme.com",
        "permissions": {
            "blackwells": "crud",
            "whsmith": "query"
        },
    },
    "bridget.huntington-whiteley@acme.com": {
        "db_username": "bridget.huntington-whiteley@acme.com"[:32],
        "permissions": {},
    },
    "valerie.tennant@acme.com": {
        "db_username": "valerie.tennant@acme.com",
        "permissions": {
            "blackwells": "crud"
        },
    },
}

SERVICES_CONFIG = {
    "glue_connections": {
        "blackwells": {
            "grant_type": "crud",
            "physical_connection_requirements": {
                "availability_zone": f"{AWS_REGION}a",
                "security_group_id_list": ["sg-93ad699f"],
                "subnet_id": "subnet-283fefc6",
            },
        },
    }
}

MASTER_PASSWORD_DEFAULTS = {
    r"([a-z][a-z0-9-]+)": r"ssm:\1.master_password"
}

OKTA_AWS_APP_ID = "7ns8u7ry8voMhQOsa644"
OKTA_API_TOKEN = "000AmAPPcvEZ8qvjY3vwh7CS6__JrRNatR3XuvaCZx"


def assert_dict_equals(actual: dict, expected: dict):
    differences = list(diff(actual, expected))
    if differences:
        # To avoid truncation of AssertionError message
        assert False, "Dict diff:\n{}".format(pformat(differences))


def initial_model() -> Prodict:
    return Prodict.from_dict({
        "aws": {
            "region": AWS_REGION,
        },
        "okta": {
            "organization": "acme",
            "aws_app": {
                "app_id": OKTA_AWS_APP_ID,
                "iam_idp": "Okta"
            },
        },
        "job": {},
    })


class TestGatherers:

    @mock_sts
    def test_aws_gather_account_info(self):
        aws_gatherer = AwsGatherer(AwsClient(AWS_REGION))
        resp, issues = aws_gatherer.gather_general_info(initial_model())
        assert_dict_equals(resp, {"aws": {"account": str(ACCOUNT_ID)}})

    @mock_ssm
    def test_cfg_gather_rds_config(self):
        client = boto3.client("ssm", region_name=AWS_REGION)
        client.put_parameter(
            Name="blackwells.master_password", Value="focused_mendel", Type="SecureString"
        )
        client.put_parameter(
            Name="whsmith.master_password", Value="quirky_ganguly", Type="SecureString"
        )
        aws_client = AwsClient(AWS_REGION)
        resp, issues = DatabaseConfigGatherer(MASTER_PASSWORD_DEFAULTS,
                                              "tests/data/databases.yaml",
                                              aws_client).gather_rds_config(initial_model())
        assert len(issues) == 1
        assert issues[0].level == IssueLevel.ERROR
        assert issues[0].type == "DB"
        assert issues[0].id == "daunt-books"
        assert_dict_equals(resp, {"aws": {"databases": RDS_CONFIG_DATABASES}})

    @mock_ec2
    @mock_rds2
    def test_aws_gather_rds_info(self):
        vpc_conn = boto3.client("ec2", region_name=AWS_REGION)
        vpc = vpc_conn.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]
        # to force a deterministic sequence of "random" numbers
        random.seed(1)
        subnet1 = vpc_conn.create_subnet(VpcId=vpc["VpcId"], CidrBlock="10.0.1.0/24",
                                         AvailabilityZone=f"{AWS_REGION}a")["Subnet"]
        subnet2 = vpc_conn.create_subnet(VpcId=vpc["VpcId"], CidrBlock="10.0.2.0/24",
                                         AvailabilityZone=f"{AWS_REGION}b")["Subnet"]
        subnet_ids = [subnet1["SubnetId"], subnet2["SubnetId"]]

        conn = boto3.client("rds", region_name=AWS_REGION)
        conn.create_db_subnet_group(
            DBSubnetGroupName="db_subnet",
            DBSubnetGroupDescription="my db subnet",
            SubnetIds=subnet_ids,
        )

        for index, db_id, db_name in [
            (1, "acme-test", "acme"),
            (2, "blackwells", "db_blackwells"),
            (3, "foyles", "db_foyles"),
        ]:
            conn.create_db_instance(
                DBInstanceIdentifier=db_id,
                Engine="mysql",
                EngineVersion="5.7.28",
                DBName=db_name,
                MasterUsername="acme",
                DBInstanceClass="db.m1.small",
                MultiAZ=True,
                AvailabilityZone=f"{AWS_REGION}a",
                VpcSecurityGroupIds=[random_security_group_id()],
                DBSubnetGroupName="db_subnet",
            )
        aws_gatherer = AwsGatherer(AwsClient(AWS_REGION))
        model = initial_model()
        model.aws["databases"] = Prodict.from_dict(RDS_CONFIG_DATABASES)
        resp, issues = aws_gatherer.gather_rds_info(model)
        assert len(issues) == 2
        assert issues[0].level == IssueLevel.WARNING
        assert issues[0].type == "DB"
        assert issues[0].id == "acme-test"
        assert issues[1].level == IssueLevel.ERROR
        assert issues[1].type == "DB"
        assert issues[1].id == "whsmith"
        assert_dict_equals(resp, {"aws": {"databases": RDS_INFO_DATABASES}})

    def test_cfg_gather_user_config(self):
        model = initial_model()
        model.aws["databases"] = Prodict.from_dict({
            "blackwells": {
                "status": "ACCESSIBLE",
                "db_name": "db_blackwells",
                "master_password": "ssm:blackwells.master_password",
            },
            "foyles": {
                "status": "DISABLED",
            },
            "blackwells-recover": {
                "status": "ABSENT",
            },
            "whsmith": {
                "status": "ENABLED",
                "db_name": "qa_results",
                "master_password": "ssm:whsmith.master_password",
            },
        })
        tz = pytz.timezone("Europe/Dublin")
        time_ref = datetime(2020, 5, 15, 22, 24, 51, tzinfo=tz)
        user_config = UserConfigGatherer("tests/data/users.yaml", time_ref)
        resp, issues = user_config.gather_user_config(model)
        assert_dict_equals(resp, {
            "job": {
                # 2020-05-26 10:22:00 +01
                "next_transition": datetime(2020, 5, 26, 9, 22, 0, tzinfo=timezone.utc)
            },
            "okta": {
                "users": USERS_CONFIG,
            },
            "aws": {
                "databases": {
                    "blackwells": {
                        "permissions": {
                            "leroy.trent@acme.com": "crud",
                            "valerie.tennant@acme.com": "crud",
                        },
                    },
                    "whsmith": {
                        "permissions": {
                            "leroy.trent@acme.com": "query",
                        },
                    },
                },
            },
        })

    def test_cfg_gather_service_config(self):
        # Given:
        model = initial_model()
        model.aws["databases"] = dict_deep_merge(Prodict.from_dict(RDS_CONFIG_DATABASES), RDS_INFO_DATABASES)
        svc_config = ServiceConfigGatherer("tests/data/services.yaml")

        # When:
        resp, issues = svc_config.gather_service_config(model)

        # Then:
        assert len(issues) == 2
        assert issues[0].level == IssueLevel.ERROR
        assert issues[0].type == "GLUE"
        assert issues[0].id == "whsmith"
        assert issues[1].level == IssueLevel.ERROR
        assert issues[1].type == "GLUE"
        assert issues[1].id == "foyles"

        assert_dict_equals(resp, {"aws": SERVICES_CONFIG})

    def test_okta_gather_user_info(self):
        model = initial_model()
        model.okta.update(Prodict(users=USERS_CONFIG))
        model.okta.users["tracy.mickelsen@acme.com"] = {
            "db_username": "tracy.mickelsen@acme.com",
            "permissions": {},
        }
        model.okta.users["miguel.heidler@acme.com"] = {
            "db_username": "miguel.heidler@acme.com",
            "permissions": {},
        }
        model.okta.aws_app.app_id = OKTA_AWS_APP_ID

        query_prefix = r"^limit=1&search=profile\.login\+eq\+"

        @urlmatch(scheme="https", netloc="acme.okta.com",
                  path=r"^/api/v1/users",
                  query=query_prefix)
        def okta_user_info(url, request):
            assert request.headers["Authorization"] == f"SSWS {OKTA_API_TOKEN}"
            m = re.match(query_prefix + r'"(.*)@acme\.com"$', unquote(url.query))
            assert m
            username = m.group(1)
            user_file = Path(f"tests/data/users/{username}.json")
            content = user_file.read_text() if user_file.exists() else "[]"
            return response(status_code=200,
                            content=content,
                            headers={
                                "Content-Type": "application/json"
                            })

        # noinspection PyUnusedLocal
        @urlmatch(scheme="https", netloc="acme.okta.com",
                  path=rf"^/api/v1/apps/{OKTA_AWS_APP_ID}/users$")
        def okta_app_users(url, request):
            # pylint: disable=W0613
            assert request.headers["Authorization"] == f"SSWS {OKTA_API_TOKEN}"
            return response(status_code=200,
                            content=Path(f"tests/data/okta_app_users.json").read_text(),
                            headers={
                                "Content-Type": "application/json"
                            })

        with ThreadPoolExecutor(max_workers=1) as executor:
            okta_gatherer = OktaGatherer(OKTA_API_TOKEN, executor)
            with HTTMock(okta_user_info, okta_app_users):
                resp, issues = okta_gatherer.gather_user_info(model)
        assert len(issues) == 3
        assert issues[0].level == IssueLevel.ERROR
        assert issues[0].type == "USER"
        assert issues[0].id == "valerie.tennant@acme.com"
        assert issues[1].level == IssueLevel.ERROR
        assert issues[1].type == "USER"
        assert issues[1].id == "tracy.mickelsen@acme.com"
        assert issues[2].level == IssueLevel.ERROR
        assert issues[2].type == "USER"
        assert issues[2].id == "miguel.heidler@acme.com"
        assert_dict_equals(resp, {"okta": {"users": {
            "valerie.tennant@acme.com": {
                "status": "INACTIVE",
            },
            "miguel.heidler@acme.com": {
                "status": "DEPROVISIONED",
            },
            "leroy.trent@acme.com": {
                "status": "ACTIVE",
                "user_id": "00m6q2lgisjgmFq64772",
                "ssh_pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEfzjdkO1LKnS/it62jmw9tH4BznlnDCBrzaKguujJ15 "
                              "leroy.trent@acme.com",
                "saml_roles": [
                    "sari_the-works",
                    "sari_waterstones",
                    "sari_blackwells"
                ],
            },
            "bridget.huntington-whiteley@acme.com": {
                "status": "ACTIVE",
                "user_id": "00u4subrvCRYYe2dx765",
                "ssh_pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGD13Dbe1QoYrFZqCue1TzGkzDSra9ZHzv8gZy9+vb0Y "
                              "bridget.huntington-whiteley@acme.com",
            },
            "tracy.mickelsen@acme.com": {
                "status": "ABSENT",
            }
        }}})
