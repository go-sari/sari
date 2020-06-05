from typing import List, Tuple, Dict

from prodict import Prodict

from main.aws_client import AwsClient
from main.dbstatus import DbStatus
from main.issue import Issue, IssueLevel
from main.password_resolver import MasterPasswordResolver
from .gatherer import Gatherer

ENGINE_TYPE = "mysql"


class DatabaseInfoGatherer(Gatherer):
    def __init__(self, aws: AwsClient, pwd_resolver: MasterPasswordResolver):
        self.aws = aws
        self.pwd_resolver = pwd_resolver

    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        issues = []
        configured_databases = model.aws.databases
        not_found = dict(status=DbStatus.ABSENT.name)
        updates = {db_uid: not_found for db_uid in configured_databases
                   if db_uid.startswith(f"{self.aws.region}/")}
        for db in self.aws.rds_enum_databases(ENGINE_TYPE):
            db_id = db["DBInstanceIdentifier"]
            db_uid = f"{self.aws.region}/{db_id}"
            if db_uid not in configured_databases:
                try:
                    master_password, password_age = self.pwd_resolver.resolve(db_id, None)
                    db_upd = {
                        "status": DbStatus.AUTO_ENABLED.name,
                        "permissions": {},
                        "master_password": master_password,
                        "password_age": password_age,
                    }
                except Exception as e:
                    issues.append(Issue(level=IssueLevel.WARNING, type="DB", id=db_uid,
                                        message=f"Failed to auto-configure: {e}"))
                    continue
            elif DbStatus[configured_databases[db_uid].status] == DbStatus.ENABLED:
                db_upd = {}
            else:
                del updates[db_uid]
                continue
            subnets_by_az = _get_subnets_by_az(db)
            az = db.get("AvailabilityZone",
                        # Chose the AZ of the first subnet arbitrarily.
                        # Required for MOTO since AZ is not defined.
                        next(iter(subnets_by_az.keys())))
            db_upd.update({
                "db_name": db["DBName"],
                "master_username": db["MasterUsername"],
                "endpoint": {
                    "address": db["Endpoint"]["Address"],
                    "port": db["Endpoint"]["Port"],
                },
                "dbi_resource_id": db["DbiResourceId"],
                "availability_zone": az,
                "vpc_security_group_ids": [sg["VpcSecurityGroupId"] for sg in db["VpcSecurityGroups"]
                                           if sg["Status"] == "active"],
                "primary_subnet": subnets_by_az[az][0]
            })
            updates[db_uid] = db_upd
        for db_uid, db in updates.items():
            if db == not_found:
                issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_uid, message="Not found in AWS"))
        return Prodict(aws={"databases": updates}), issues


def _get_subnets_by_az(db) -> Dict[str, List[str]]:
    subnets = {}
    for sn in db["DBSubnetGroup"]["Subnets"]:
        if sn["SubnetStatus"] == "Active":
            subnets.setdefault(sn["SubnetAvailabilityZone"]["Name"], []).append(sn["SubnetIdentifier"])
    return subnets
