import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

import pulumi
import pulumi_aws
import pulumi_aws.cloudwatch as cloudwatch
import pulumi_aws.glue as glue
import pulumi_aws.iam as iam
import pulumi_mysql as mysql
import pulumi_okta
import pulumi_okta.app as okta_app
import pulumi_random as random
from loguru import logger
from paramiko import SSHException
from prodict import Prodict

from main.bastion_host import update_authorized_keys
from main.dbstatus import DbStatus

ANY_HOST = "%"

SARI_ROLE_NAME = "SARI"


class Synthesizer:

    def __init__(self, config: Prodict, model: Prodict):
        self.config = config
        self.model = model
        self.default_provider = pulumi_aws.Provider("default")
        self.aws_providers = {region: pulumi_aws.Provider(region, region=region) for region in model.aws.regions}
        self._mysql_providers = {}
        self.standard_tags = {
            "Provisioning": "SARI",
            "sari:configuration": _get_sari_configuration_repo(),
            "sari:project": _get_ci_project_url(),
        }

    def synthesize_all(self):
        self.synthesize_cloudwatch()
        self.synthesize_iam()
        self.synthesize_mysql()
        self.synthesize_glue_connections()
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
                             tags=self.standard_tags,
                             description="Trigger SARI Build for Next Transition",
                             schedule_expression=f"cron({dt.minute} {dt.hour} {dt.day} {dt.month} ? {dt.year})",
                             opts=pulumi.ResourceOptions(provider=self.default_provider))
        cloudwatch.EventTarget(rule_name,
                               arn=f"arn:aws:codebuild:{aws.default_region}:{aws.account}:project/build-sari",
                               role_arn=f"arn:aws:iam::{aws.account}:role/service-role/build-sari-start",
                               rule=rule_name,
                               opts=pulumi.ResourceOptions(provider=self.default_provider))

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
                        tags=self.standard_tags,
                        assume_role_policy=assume_role_policy,
                        opts=pulumi.ResourceOptions(provider=self.default_provider))
        db_policy = _aws_make_policy(
            [{
                "Sid": "DescribeDBInstances",
                "Effect": "Allow",
                "Action": "rds:DescribeDBInstances",
                "Resource": "*"
            }] + [{
                "Effect": "Allow",
                "Action": "rds-db:connect",
                "Resource": f"arn:aws:rds-db:*:{aws.account}:dbuser:*/{login}"
            } for login, user in self.model.okta.users.items()
                if user.status == "ACTIVE" and user.permissions]
        )
        iam.RolePolicy("sari",
                       name=SARI_ROLE_NAME,
                       role=role.id,
                       policy=db_policy,
                       opts=pulumi.ResourceOptions(provider=self.default_provider))

    def synthesize_mysql(self):
        for login, user in self.model.okta.users.items():
            if user.status != "ACTIVE":
                continue
            for db_uid, grant_type in user.permissions.items():
                db = self.model.aws.databases[db_uid]
                if DbStatus[db.status] < DbStatus.ACCESSIBLE:
                    continue
                provider = self._get_mysql_provider(db_uid, db)
                region, db_id = db_uid.split("/")
                old_resource_name = f"{db_id}/{login}"
                resource_name = f"{db_uid}/{login}"
                mysql_user = mysql.User(resource_name,
                                        user=login,
                                        host=ANY_HOST,
                                        auth_plugin="AWSAuthenticationPlugin",
                                        tls_option="SSL",
                                        opts=pulumi.ResourceOptions(
                                            provider=provider,
                                            delete_before_replace=True,
                                            aliases=[_pulumi_arn("mysql:index/user:User", old_resource_name)]
                                        ))
                mysql.Grant(resource_name,
                            user=mysql_user.user,
                            database=db.db_name,
                            host=ANY_HOST,
                            privileges=self.config.grant_types[grant_type],
                            opts=pulumi.ResourceOptions(
                                provider=provider,
                                delete_before_replace=True,
                                aliases=[_pulumi_arn("mysql:index/grant:Grant", old_resource_name)]
                            ))

    def synthesize_okta(self):
        okta = self.model.okta
        provider = pulumi_okta.Provider("default",
                                        api_token=pulumi.Output.secret(okta.api_token),
                                        org_name=okta.organization)
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

    def synthesize_glue_connections(self):
        glue_connections = self.model.aws.glue_connections
        databases = self.model.aws.databases
        for db_uid, con in glue_connections.items():
            db = databases[db_uid]
            region, db_id = db_uid.split("/")
            old_resource_name = f"glue/{db_id}"
            resource_name = f"glue/{db_uid}"
            password = random.RandomPassword(resource_name,
                                             length=64,
                                             special=False,
                                             opts=pulumi.ResourceOptions(additional_secret_outputs=["result"]))
            login = "glue.amazonaws.com"
            mysql_provider = self._get_mysql_provider(db_uid, db)
            mysql_user = mysql.User(resource_name,
                                    user=login,
                                    host=ANY_HOST,
                                    plaintext_password=password.result,
                                    tls_option="SSL",
                                    opts=pulumi.ResourceOptions(
                                        provider=mysql_provider,
                                        delete_before_replace=True,
                                        aliases=[_pulumi_arn("mysql:index/user:User", old_resource_name)]
                                    ))
            mysql.Grant(resource_name,
                        user=mysql_user.user,
                        database=db.db_name,
                        host=mysql_user.host,
                        privileges=self.config.grant_types[con.grant_type],
                        opts=pulumi.ResourceOptions(
                            provider=mysql_provider,
                            delete_before_replace=True,
                            aliases=[_pulumi_arn("mysql:index/grant:Grant", old_resource_name)]
                        ))
            region, db_id = db_uid.split("/")
            aws_provider = self.aws_providers[region]
            glue.Connection(resource_name,
                            name=f"sari.{db_id}",
                            description="Provisioned by SARI -- DO NOT EDIT",
                            connection_type="JDBC",
                            connection_properties={
                                "JDBC_CONNECTION_URL": f"jdbc:mysql://{db.endpoint.address}:{db.endpoint.port}/"
                                                       f"{db.db_name}",
                                "JDBC_ENFORCE_SSL": "true",
                                "USERNAME": login,
                                "PASSWORD": password.result,
                            },
                            physical_connection_requirements=con.physical_connection_requirements,
                            opts=pulumi.ResourceOptions(
                                provider=aws_provider,
                                delete_before_replace=True,
                                aliases=[_pulumi_arn("aws:glue/connection:Connection", old_resource_name)]
                            ))

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
            logger.error(f"Errors while updating Bastion Host: {e}")

    def _get_mysql_provider(self, db_uid, db):
        provider = self._mysql_providers.get(db_uid, None)
        if not provider:
            provider = mysql.Provider(db_uid,
                                      endpoint=f"{db.endpoint.address}:{db.endpoint.port}",
                                      proxy=self.config.system.proxy,
                                      username=db.master_username,
                                      password=pulumi.Output.secret(db.plain_master_password))
            self._mysql_providers[db_uid] = provider
        return provider


def _pulumi_arn(type_: str, name: str) -> str:
    return f"urn:pulumi:{pulumi.get_stack()}::{pulumi.get_project()}::{type_}::{name}"


def _get_sari_configuration_repo():
    return os.environ.get("CODEBUILD_SOURCE_REPO_URL") or os.environ.get("CONFIG") or "UNKNOWN"


def _get_ci_project_url():
    cb_arn = os.environ.get("CODEBUILD_BUILD_ARN")
    if cb_arn:
        _, _, _, region, account, path, *_ = cb_arn.split(":")
        return f"https://{region}.console.aws.amazon.com/codesuite/codebuild/{account}/" \
               f"{path.replace('build/', 'projects/')}"
    return "UNKNOWN"


def _aws_make_policy(statements: List[dict]) -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": statements
    })
