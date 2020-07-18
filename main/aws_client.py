from datetime import datetime
from functools import lru_cache
from typing import List, Tuple, Set

import boto3
from botocore.client import BaseClient
from configobj import ConfigObj


class AwsClient:
    _rds_known_endpoints: Set[str] = set()

    def __init__(self, aws_region: str = None):
        self._session = boto3.session.Session(region_name=aws_region)
        self._clients = {}

    @classmethod
    def get_rds_known_endpoints(cls):
        return cls._rds_known_endpoints

    @property
    def region(self):
        return self._session.region_name

    def get_account_id(self) -> str:
        """
        Get the AWS account number.
        """
        sts = self._get_client('sts')
        return sts.get_caller_identity()['Account']

    def rds_enum_databases(self, engine_type: str) -> List[dict]:
        rds = self._get_client("rds")
        paginator = rds.get_paginator("describe_db_instances")
        databases = []
        for page in paginator.paginate(PaginationConfig={"MaxItems": 1000}):
            databases.extend(db for db in page["DBInstances"] if db["Engine"] == engine_type)
        try:
            for db in databases:
                self._rds_known_endpoints.add(f"{db['Endpoint']['Address']}:{db['Endpoint']['Port']}")
        except KeyError:
            pass
        return databases

    def ssm_get_encrypted_parameter(self, name) -> Tuple[str, datetime]:
        ssm = self._get_client('ssm')
        parameter = ssm.get_parameter(Name=name, WithDecryption=True)['Parameter']
        return parameter['Value'], parameter.get('LastModifiedDate', None)

    def s3_get_property(self, bucket_name, key, property_name) -> Tuple[str, datetime]:
        s3 = self._get_client('s3')
        s3_object = s3.get_object(Bucket=bucket_name, Key=key)
        body = s3_object['Body'].read().decode("utf-8")
        last_modified = s3_object['LastModified']
        properties = ConfigObj(body.splitlines())
        return properties[property_name], last_modified

    @lru_cache(maxsize=None)
    def _get_client(self, service_name) -> BaseClient:
        return self._session.client(service_name)
