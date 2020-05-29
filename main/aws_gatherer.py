from typing import List, Tuple, Dict

from prodict import Prodict

from main.aws_client import AwsClient
from main.dbstatus import DbStatus
from main.issue import Issue, IssueLevel

ENGINE_TYPE = "mysql"


class AwsGatherer:
    def __init__(self, aws: AwsClient):
        self.aws = aws

    # noinspection PyUnusedLocal
    def gather_account_info(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        # pylint: disable=W0613
        """
        Get the AWS account number.
        """
        return Prodict(aws={"account": self.aws.get_account_id()}), []

    def gather_rds_info(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        issues = []
        configured_databases = model.aws.databases
        not_found = dict(status=DbStatus.ABSENT.name)
        updates = {db_id: not_found for db_id in configured_databases}
        for db in self.aws.rds_enum_databases(ENGINE_TYPE):
            db_id = db["DBInstanceIdentifier"]
            if db_id in configured_databases:
                if DbStatus[configured_databases[db_id].status] == DbStatus.ENABLED:
                    subnets_by_az = _get_subnets_by_az(db)
                    az = db.get("AvailabilityZone", next(iter(subnets_by_az.keys())))
                    updates[db_id] = {
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
                    }
                else:
                    del updates[db_id]
            else:
                issues.append(Issue(level=IssueLevel.WARNING, type="DB", id=db_id,
                                    message="Present in AWS but NOT configured"))
        for db_id, db in updates.items():
            if db == not_found:
                issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_id,
                                    message="Not found in AWS"))
        return Prodict(aws={"databases": updates}), issues


def _get_subnets_by_az(db) -> Dict[str, List[str]]:
    subnets = {}
    for sn in db["DBSubnetGroup"]["Subnets"]:
        if sn["SubnetStatus"] == "Active":
            subnets.setdefault(sn["SubnetAvailabilityZone"]["Name"], []).append(sn["SubnetIdentifier"])
    return subnets
