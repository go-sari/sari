from datetime import datetime
from typing import List, Tuple

import boto3
from botocore.client import BaseClient
from configobj import ConfigObj


class AwsClient:
    def __init__(self, aws_region: str):
        self._session = boto3.session.Session(region_name=aws_region)
        self._clients = {}

    def get_account_id(self) -> str:
        """
        Get the AWS account number.
        """
        sts = self._get_session('sts')
        return sts.get_caller_identity()['Account']

    def rds_enum_databases(self, engine_type: str) -> List[dict]:
        rds = self._get_session('rds')
        databases = rds.describe_db_instances()['DBInstances']
        return [db for db in databases if db["Engine"] == engine_type]

    def ssm_get_encrypted_parameter(self, name) -> Tuple[str, datetime]:
        ssm = self._get_session('ssm')
        parameter = ssm.get_parameter(Name=name, WithDecryption=True)['Parameter']
        return parameter['Value'], parameter.get('LastModifiedDate', None)

    def s3_get_property(self, bucket_name, key, property_name) -> Tuple[str, datetime]:
        s3 = self._get_session('s3')
        s3_object = s3.get_object(Bucket=bucket_name, Key=key)
        body = s3_object['Body'].read().decode("utf-8")
        last_modified = s3_object['LastModified']
        properties = ConfigObj(body.splitlines())
        return properties[property_name], last_modified

    def _get_session(self, service_name) -> BaseClient:
        if service_name not in self._clients:
            self._clients[service_name] = self._session.client(service_name)
        return self._clients[service_name]
