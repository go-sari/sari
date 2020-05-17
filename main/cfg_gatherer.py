import json
import re
from datetime import datetime, timedelta
from distutils.util import strtobool
from typing import Dict, List, Optional, Tuple, Union

import pytz
import yaml
from prodict import Prodict
from time import time

from main.aws_client import AwsClient
from main.dbstatus import DbStatus
from main.issue import Issue, IssueLevel
from main.misc import wc_expand

# Limited by MySQL. See https://dev.mysql.com/doc/refman/8.0/en/user-names.html
MAX_DB_USERNAME_LENGTH = 32
DEFAULT_GRANT_TYPE = 'query'


class UserConfigGatherer:
    def __init__(self, cfg_filename: str, time_ref: datetime = None):
        """
        :param cfg_filename: Path of Yaml file containing users definition.

        :param time_ref: (Aware) datetime to evaluate validity times.
        """
        self.cfg_filename = cfg_filename
        if not time_ref:
            time_ref = datetime.now(pytz.utc)
        else:
            _check_dt(time_ref, "time_ref")
        self.time_ref = time_ref
        self._next_transition = None

    def gather_user_config(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        self._next_transition = model.job.next_transition
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
            try:
                permissions = self._parse_permissions(
                    user.get("permissions", []),
                    default_grant_type,
                    enabled_databases)
                users[login] = {
                    "db_username": login[:MAX_DB_USERNAME_LENGTH],
                    "permissions": permissions
                }
                for db_id, grant_type in permissions.items():
                    databases.setdefault(db_id, {"permissions": {}})["permissions"][login] = grant_type
            except ValueError as e:
                issues.append(Issue(level=IssueLevel.ERROR, type='USER', id=login, message=str(e)))
        updates = Prodict(okta={"users": users}, aws={"databases": databases})
        if self._next_transition:
            updates.job = dict(next_transition=self._next_transition)
        return updates, issues

    def _parse_permissions(self, perm_list: List[dict],
                           default_grant_type: str,
                           db_ids: List[str]) -> Dict[str, str]:
        permissions: Dict[str, str] = {}
        for perm in perm_list:
            db_ref = perm['db']
            db_id_list = wc_expand(db_ref, db_ids)
            if not db_id_list:
                raise ValueError(f"Not existing and enabled DB instance reference '{db_ref}'")
            not_valid_before = _check_dt(perm, "not_valid_before")
            not_valid_after = _check_dt(perm, "not_valid_after")
            grant_type = perm.get('grant_type', default_grant_type)
            if not_valid_before or not_valid_after:
                if not_valid_before and not_valid_after and (not_valid_after < not_valid_before):
                    raise ValueError(f"'{not_valid_before}' should precede '{not_valid_after}'")
                if not_valid_before and self.time_ref < not_valid_before:
                    grant_type = default_grant_type
                    self._set_next_transition(not_valid_before)
                elif not_valid_after:
                    if self.time_ref > not_valid_after:
                        grant_type = default_grant_type
                    else:
                        self._set_next_transition(not_valid_after)
            if grant_type != "none":
                for db_id in db_id_list:
                    permissions[db_id] = grant_type
        return permissions

    def _set_next_transition(self, dt: datetime):
        if not self._next_transition or (dt < self._next_transition):
            self._next_transition = dt


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


class ManagedUsersGatherer:
    def __init__(self, cfg_filename: str):
        self.cfg_filename = cfg_filename

    def gather_managed_users(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        # Pulumi specific pattern
        pat = re.compile(r"urn:pulumi:.*::mysql:index/user:User::([a-z-]+)/(\S+)")
        with open(self.cfg_filename) as file:
            urn_list: List[str] = json.load(file)
        db_users = {}
        db_id_list = model.aws.databases.keys()
        for urn in urn_list:
            m = pat.match(urn)
            if m:
                db_id, login = (m.group(1), m.group(2))
                if db_id in db_id_list:
                    db_users.setdefault(db_id, set()).add(login)
        updates = {db_id: dict(managed_users=users) for db_id, users in db_users.items()}
        return Prodict(aws={"databases": updates}), []


def _to_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return bool(strtobool(val))


def _check_dt(dt: Union[datetime, dict], name: str) -> Optional[datetime]:
    if isinstance(dt, dict):
        dt = dt.get(name, None)
    if dt:
        if not dt.tzinfo:
            raise ValueError(f"{name} must be None or an aware-datetime")
        return dt.astimezone(pytz.UTC)
    return None
