import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

import pulumi
import pulumi_aws
import pulumi_aws.cloudwatch as cloudwatch
import pulumi_aws.iam as iam
import pulumi_mysql as mysql
import pulumi_okta
import pulumi_okta.app as okta_app
from loguru import logger
from paramiko import SSHException
from prodict import Prodict

from main.bastion_host import update_authorized_keys
from main.dbstatus import DbStatus

SARI_ROLE_NAME = "SARI"


class Synthesizer:

    def __init__(self, config: Prodict, model: Prodict):
        self.config = config
        self.model = model
        self.aws_provider = pulumi_aws.Provider("default", region=model.aws.region)

    def synthesize_all(self):
        self.synthesize_cloudwatch()
        self.synthesize_iam()
        self.synthesize_mysql()
        self.synthesize_okta()
        self.synthesize_bastion_host()

    def synthesize_cloudwatch(self):
        dt: datetime = self.model.job.next_transition
        if not dt:
            return
        aws = self.model.aws
        rule_name = "build-sari-start"
        cloudwatch.EventRule(rule_name,
                             name=rule_name,
                             description="Trigger SARI Build for Next Transition",
                             schedule_expression=f"cron({dt.minute} {dt.hour} {dt.day} {dt.month} ? {dt.year})",
                             opts=pulumi.ResourceOptions(provider=self.aws_provider))
        cloudwatch.EventTarget(rule_name,
                               arn=f"arn:aws:codebuild:{aws.region}:{aws.account}:project/build-sari",
                               role_arn=f"arn:aws:iam::{aws.account}:role/service-role/build-sari-start",
                               rule=rule_name,
                               opts=pulumi.ResourceOptions(provider=self.aws_provider))

    def synthesize_iam(self):
        aws = self.model.aws
        okta = self.model.okta
        assume_role_policy = _aws_make_policy([{
            "Sid": "1",
            "Effect": "Allow",
            "Principal": {
                "Federated": f"arn:aws:iam::{aws.account}:saml-provider/{okta.aws_app.iam_idp}"
            },
            "Action": "sts:AssumeRoleWithSAML",
            "Condition": {
                "StringEquals": {
                    "SAML:aud": "https://signin.aws.amazon.com/saml"
                }
            }
        }])
        role = iam.Role("sari",
                        name=SARI_ROLE_NAME,
                        description=f"Allow access to SARI-enabled databases",
                        assume_role_policy=assume_role_policy,
                        opts=pulumi.ResourceOptions(provider=self.aws_provider))
        db_policy = _aws_make_policy(
            [{
                "Sid": "DescribeDBInstances",
                "Effect": "Allow",
                "Action": "rds:DescribeDBInstances",
                "Resource": "*"
            }] + [{
                "Effect": "Allow",
                "Action": "rds-db:connect",
                "Resource": f"arn:aws:rds-db:{aws.region}:{aws.account}:dbuser:*/{login}"
            } for login, user in self.model.okta.users.items()
                if user.status == "ACTIVE" and user.permissions]
        )
        iam.RolePolicy("sari",
                       name=SARI_ROLE_NAME,
                       role=role.id,
                       policy=db_policy,
                       opts=pulumi.ResourceOptions(provider=self.aws_provider))

    def synthesize_mysql(self):
        providers = {}
        for login, user in self.model.okta.users.items():
            if user.status != "ACTIVE":
                continue
            for db_id, grant_type in user.permissions.items():
                db = self.model.aws.databases[db_id]
                if DbStatus[db.status] < DbStatus.ACCESSIBLE:
                    continue
                provider = providers.get(db_id, None)
                if not provider:
                    provider = mysql.Provider(db_id,
                                              endpoint=f"{db.endpoint.address}:{db.endpoint.port}",
                                              proxy=self.config.system.proxy,
                                              username=db.master_username,
                                              # TODO: use password retrieved from SSM at execution time
                                              password=db.plain_master_password)
                    providers[db_id] = provider
                resource_name = f"{db_id}/{login}"
                mysql_user = mysql.User(resource_name,
                                        user=login,
                                        host="%",
                                        auth_plugin="AWSAuthenticationPlugin",
                                        tls_option="SSL",
                                        opts=pulumi.ResourceOptions(provider=provider))
                mysql.Grant(resource_name,
                            user=login,
                            database=db.db_name,
                            host="%",
                            privileges=self.config.grant_types[grant_type],
                            opts=pulumi.ResourceOptions(
                                provider=provider,
                                depends_on=[mysql_user]
                            ))

    def synthesize_okta(self):
        okta = self.model.okta
        provider = pulumi_okta.Provider("default", org_name=okta.organization)
        app_id = okta.aws_app.app_id
        for login, user in okta.users.items():
            if user.status != "ACTIVE":
                continue
            okta_app.User(login,
                          app_id=app_id,
                          user_id=user.user_id,
                          username=login,
                          profile=json.dumps({
                              "email": login,
                              "samlRoles": [SARI_ROLE_NAME]
                          }),
                          opts=pulumi.ResourceOptions(provider=provider))

    def synthesize_bastion_host(self):
        ssh_users = {login: user.ssh_pubkey for login, user in self.model.okta.users.items()
                     if user.status == "ACTIVE"}
        bh = self.model.bastion_host
        if bh.admin_private_key:
            _, key_filename = tempfile.mkstemp(text=True)
            Path(key_filename).write_text(bh.admin_private_key)
        else:
            key_filename = bh.admin_key_filename or f"{self.config.system.config_dir}/admin_id_rsa"
        logger.info("Enabling SSH access to Bastion Host:")
        try:
            errors = update_authorized_keys(hostname=bh.hostname,
                                            admin_username=bh.admin_username,
                                            key_filename=key_filename,
                                            passphrase=bh.admin_key_passphrase,
                                            username=bh.proxy_username,
                                            ssh_pub_keys=list(ssh_users.values()),
                                            port=bh.port)
            if errors:
                logger.error("Errors while updating Bastion Host")
                for err in errors:
                    logger.error(err.strip())
            elif ssh_users:
                for login in ssh_users:
                    logger.info(f"  {login}")
            else:
                logger.info(f"  NONE")
        except SSHException as e:
            logger.error("Errors while updating Bastion Host: {e}")


def _aws_make_policy(statements: List[dict]) -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": statements
    })
