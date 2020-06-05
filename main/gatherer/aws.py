from typing import List, Tuple

from prodict import Prodict

from main.aws_client import AwsClient
from main.issue import Issue
from .gatherer import Gatherer


class AwsGatherer(Gatherer):
    def __init__(self, aws: AwsClient):
        self.aws = aws

    # noinspection PyUnusedLocal
    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        # pylint: disable=W0613
        """
        Get the AWS account number.
        """
        return Prodict(aws={"account": self.aws.get_account_id()}), []
