from datetime import datetime
from distutils.util import strtobool
from io import StringIO
from typing import Dict, List, Optional, Tuple, Union

import pytz
import yaml
from prodict import Prodict

from main.domain import DbStatus, Issue, IssueLevel
from main.util import wc_expand
from .gatherer import Gatherer
from .pwd_resolver import MasterPasswordResolver

# Limited by MySQL. See https://dev.mysql.com/doc/refman/5.7/en/user-names.html
MAX_DB_USERNAME_LENGTH = 32
DEFAULT_GRANT_TYPE = 'query'


class UserConfigGatherer(Gatherer):
    def __init__(self, cfg_stream: Union[str, StringIO], time_ref: datetime = None):
        """
        :param cfg_stream: Path of Yaml file containing users definition.

        :param time_ref: (Aware) datetime to evaluate validity times.
        """
        self.cfg_stream = cfg_stream
        if not time_ref:
            time_ref = datetime.now(pytz.utc)
        else:
            _check_dt(time_ref, "time_ref")
        self.time_ref = time_ref
        self._next_transition = None

    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        self._next_transition = model.job.next_transition
        issues = []
        with _open(self.cfg_stream) as stream:
            users_list: List[dict] = yaml.safe_load(stream) or []
        default_db_name = {db_uid: db.db_name for db_uid, db in model.aws.databases.items()
                           if DbStatus[db.status] >= DbStatus.ENABLED}
        enabled_databases = list(default_db_name.keys())
        users = {}
        databases = {}
        for user in users_list:
            login = user["login"]
            default_grant_type = user.get("default_grant_type", DEFAULT_GRANT_TYPE)
            try:
                permissions = self._parse_permissions(
                    user.get("permissions", []),
                    model.aws.single_region,
                    default_grant_type,
                    enabled_databases,
                    default_db_name)
                users[login] = {
                    "db_username": login[:MAX_DB_USERNAME_LENGTH],
                    "permissions": permissions
                }
                for db_uid, grant_type in permissions.items():
                    databases.setdefault(db_uid, {"permissions": {}})["permissions"][login] = grant_type
            except ValueError as e:
                issues.append(Issue(level=IssueLevel.ERROR, type='USER', id=login, message=str(e)))
        updates = Prodict(okta={"users": users}, aws={"databases": databases})
        if self._next_transition:
            updates.job = dict(next_transition=self._next_transition)
        return updates, issues

    def _parse_permissions(self, perm_list: List[dict],
                           default_region: Optional[str],
                           default_grant_type: str,
                           db_ids: List[str],
                           default_db_name: Dict[str, dict]) -> Dict[str, dict]:
        permissions: Dict[str, dict] = {}
        for perm in perm_list:
            db_ref = perm['db']
            if "/" not in db_ref and default_region:
                db_ref = f"{default_region}/{db_ref}"
            db_id_list = wc_expand(db_ref, db_ids)
            if not db_id_list:
                raise ValueError(f"Not existing and enabled DB instance reference '{db_ref}'")
            not_valid_before = _check_dt(perm, "not_valid_before")
            not_valid_after = _check_dt(perm, "not_valid_after")
            db_names = perm.get('db_names')
            if isinstance(db_names, str):
                db_names = [db_names]
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
                for db_uid in db_id_list:
                    permissions[db_uid] = dict(
                        db_names=(db_names or [default_db_name[db_uid]]),
                        grant_type=grant_type
                    )
        return permissions

    def _set_next_transition(self, dt: datetime):
        if not self._next_transition or (dt < self._next_transition):
            self._next_transition = dt


class DatabaseConfigGatherer(Gatherer):
    def __init__(self, region: str, cfg_filename: str, pwd_resolver: MasterPasswordResolver):
        """
        :param region: AWS region name.
        :param cfg_filename: Path of Yaml file containing users definition.
        """
        self.region = region
        self.cfg_filename = cfg_filename
        self.pwd_resolver = pwd_resolver

    # noinspection PyUnusedLocal
    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        # pylint: disable=W0613
        with open(self.cfg_filename) as file:
            rds_list: List[dict] = yaml.safe_load(file)
        issues = []
        databases = {}
        for cfg_db in rds_list:
            db_id = cfg_db["id"]
            db_uid = f"{self.region}/{db_id}"
            enabled = _to_bool(cfg_db.setdefault("enabled", True))
            if enabled:
                try:
                    master_password, password_age = self.pwd_resolver.resolve(db_id, cfg_db.get("master_password"))
                    db = {
                        "status": DbStatus.ENABLED.name,
                        "permissions": {},
                        "master_password": master_password,
                        "password_age": password_age,
                    }
                except Exception as e:
                    issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_uid, message=str(e)))
                    continue
            else:
                db = dict(status=DbStatus.DISABLED.name)
            databases[db_uid] = db
        return Prodict(aws={"databases": databases}), issues


class ServiceConfigGatherer(Gatherer):
    def __init__(self, cfg_filename: str):
        self.cfg_filename = cfg_filename

    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        issues = []
        updates = {}
        with open(self.cfg_filename) as file:
            services = yaml.safe_load(file)
        enabled_databases = [db_uid for db_uid, db in model.aws.databases.items()
                             if DbStatus[db.status] >= DbStatus.ENABLED]
        for conn in services.get("glue_connections", []):
            db_ref = conn['db']
            db_id_list = wc_expand(db_ref, enabled_databases)
            if not db_id_list:
                issues.append(Issue(level=IssueLevel.ERROR, type='GLUE', id=db_ref,
                                    message=f"Not existing and enabled DB instance reference '{db_ref}'"))
                continue
            pcr = conn.get("physical_connection_requirements", {})
            supplied_db_names = conn.get("db_names")
            if isinstance(supplied_db_names, str):
                supplied_db_names = [supplied_db_names]
            grant_type = conn.get("grant_type", DEFAULT_GRANT_TYPE)
            for db_uid in db_id_list:
                db = model.aws.databases[db_uid]
                updates[db_uid] = {
                    "db_names": supplied_db_names or [db.db_name],
                    "grant_type": grant_type,
                    "physical_connection_requirements": {
                        "availability_zone": pcr.get("availability_zone", db.availability_zone),
                        "security_group_id_list": pcr.get("security_group_id_list", db.vpc_security_group_ids),
                        "subnet_id": pcr.get("subnet_id", db.primary_subnet),
                    },
                }
        return Prodict(aws={"glue_connections": updates}), issues


def _open(stream):
    if isinstance(stream, str):
        return open(stream, "r")
    return stream


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
