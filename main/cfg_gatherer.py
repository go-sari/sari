import re
from datetime import datetime, timedelta
from distutils.util import strtobool
from typing import Dict, List, Tuple

import yaml
from prodict import Prodict
from time import time

from main.aws_client import AwsClient
from main.dbstatus import DbStatus
from main.issue import Issue, IssueLevel
from main.misc import wc_expand
from main.period import Validator

# Limited by MySQL. See https://dev.mysql.com/doc/refman/8.0/en/user-names.html
MAX_DB_USERNAME_LENGTH = 32
DEFAULT_GRANT_TYPE = 'query'


class UserConfigGatherer:
    def __init__(self, cfg_filename: str, validator: Validator):
        """
        :param cfg_filename: Path of Yaml file containing users definition.

        :param validator: Helper class responsible to validate datetime periods.
        """
        self.cfg_filename = cfg_filename
        self.validator = validator

    def gather_user_config(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        issues = []
        with open(self.cfg_filename) as file:
            users_list: List[dict] = yaml.safe_load(file)
        enabled_databases = [db_id for db_id, db in model.aws.databases.items()
                             if DbStatus[db.status] >= DbStatus.ENABLED]
        users = {}
        databases = {}
        for user in users_list:
            login = user["login"]
            default_grant_type = user.get("default_grant_type", DEFAULT_GRANT_TYPE)
            permissions, perm_issues = self._parse_permissions(
                login,
                user.get("permissions", []),
                default_grant_type,
                enabled_databases)
            issues.extend(perm_issues)
            users[login] = {
                "db_username": login[:MAX_DB_USERNAME_LENGTH],
                "permissions": permissions
            }
            for db_id, grant_type in permissions.items():
                databases.setdefault(db_id, {"permissions": {}})["permissions"][login] = grant_type
        return Prodict(okta={"users": users}, aws={"databases": databases}), issues

    def _parse_permissions(self, login: str,
                           perm_list: List[dict],
                           default_grant_type: str,
                           db_ids: List[str]) -> Tuple[Dict[str, str], List[Issue]]:
        issues = []
        permissions: Dict[str, str] = {}
        for perm in perm_list:
            db_ref = perm['db']
            db_id_list = wc_expand(db_ref, db_ids)
            if not db_id_list:
                issues.append(Issue(level=IssueLevel.ERROR, type='USER', id=login,
                                    message=f"Not existing and enabled DB instance reference '{db_ref}'"))
                continue
            not_valid_before = perm.get('not_valid_before', None)
            not_valid_after = perm.get('not_valid_after', None)
            grant_type = perm.get('grant_type', default_grant_type) \
                if self.validator.is_valid(not_valid_before, not_valid_after) else default_grant_type
            if grant_type != "none":
                for db_id in db_id_list:
                    permissions[db_id] = grant_type
        return permissions, issues


class DatabaseConfigGatherer:
    def __init__(self, master_password_defaults: Dict[str, str], cfg_filename: str, aws: AwsClient):
        """
        :param cfg_filename: Path of Yaml file containing users definition.
        """
        self.master_password_defaults = master_password_defaults
        self.cfg_filename = cfg_filename
        self.aws = aws

    # noinspection PyUnusedLocal
    def gather_rds_config(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        # pylint: disable=W0613
        with open(self.cfg_filename) as file:
            rds_list: List[dict] = yaml.safe_load(file)
        issues = []
        databases = {}
        for cfg_db in rds_list:
            db_id = cfg_db["id"]
            enabled = _to_bool(cfg_db.setdefault("enabled", True))
            if enabled:
                db = {
                    "status": DbStatus.ENABLED.name,
                    "permissions": {},
                }
                master_password = cfg_db.get("master_password", self._infer_master_password(db_id))
                if master_password:
                    try:
                        db.update(self._expand_password(master_password))
                    except Exception as e:
                        # TODO: test this case
                        issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_id,
                                            message=f"Unable to expand master_password: {e}"))
                        continue
                else:
                    issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_id,
                                        message="Undefined master_password"))
                    continue
            else:
                db = dict(status=DbStatus.DISABLED.name)
            databases[db_id] = db
        return Prodict(aws={"databases": databases}), issues

    def _expand_password(self, master_password):
        ssm_master_password = False
        pwd_last_modified = None
        if master_password.startswith('ssm:'):
            ssm_master_password = master_password[4:]
            master_password, pwd_last_modified = self.aws.ssm_get_encrypted_parameter(ssm_master_password) \
                if ssm_master_password else (False, False)
        elif master_password.startswith('s3-prop:'):
            s3_path = master_password[8:]
            match = re.match(r"([^\s/]+)/(\S+)\[(\S+)\]", s3_path)
            if not match:
                raise ValueError(f"Invalid s3-prop reference: {s3_path}")
            bucket_name, key, property_name = match.groups()
            master_password, pwd_last_modified = self.aws.s3_get_property(bucket_name, key, property_name)
        if isinstance(pwd_last_modified, datetime):
            password_age = timedelta(seconds=(time() - pwd_last_modified.timestamp())).days
        else:
            password_age = False
        return {
            'plain_master_password': master_password,
            'ssm_master_password': ssm_master_password,
            'password_age': password_age,
        }

    def _infer_master_password(self, db_id: str):
        for pattern, template in self.master_password_defaults.items():
            match = re.match(pattern, db_id)
            if match:
                return match.expand(template)
        return None


def _to_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return bool(strtobool(val))
