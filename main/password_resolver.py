import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import pytz

from main.aws_client import AwsClient


class MasterPasswordResolver:
    def __init__(self, aws: AwsClient, regex_patterns: Dict[str, str], time_ref: datetime = None):
        if not time_ref:
            time_ref = datetime.now(pytz.utc)
        self.time_ref = time_ref
        self.aws = aws
        self.regex_patterns = regex_patterns

    def resolve(self, db_id, master_password: Optional[str]) -> Tuple[str, Optional[int]]:
        if not master_password:
            master_password = self._infer_master_password(db_id)
        return self._expand_password(master_password)

    def _infer_master_password(self, db_id: str):
        for pattern, template in self.regex_patterns.items():
            match = re.match(pattern, db_id)
            if match:
                return match.expand(template)
        raise ValueError("Undefined master_password")

    def _expand_password(self, master_password) -> Tuple[str, Optional[int]]:
        pwd_last_modified = None
        if master_password.startswith('ssm:'):
            master_password, pwd_last_modified = self.aws.ssm_get_encrypted_parameter(master_password[4:])
        elif master_password.startswith('s3-prop:'):
            s3_path = master_password[8:]
            match = re.match(r"(?P<bucket_name>[^\s/]+)/(?P<key>\S+)\[(?P<property_name>\S+)\]", s3_path)
            if not match:
                raise ValueError(f"Invalid s3-prop reference: {s3_path}")
            master_password, pwd_last_modified = self.aws.s3_get_property(match.group("bucket_name"),
                                                                          match.group("key"),
                                                                          match.group("property_name"))
        if isinstance(pwd_last_modified, datetime):
            password_age = timedelta(seconds=(self.time_ref.timestamp() - pwd_last_modified.timestamp())).days
        else:
            password_age = False
        return master_password, password_age
