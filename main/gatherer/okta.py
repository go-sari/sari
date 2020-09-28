import json
import urllib.parse
from concurrent.futures.thread import ThreadPoolExecutor
from typing import List, Tuple

import jmespath
from loguru import logger
from prodict import Prodict

from main.domain import Issue, IssueLevel
from main.util import async_retryable_session
from .gatherer import Gatherer


class OktaGatherer(Gatherer):

    def __init__(self, api_token, executor: ThreadPoolExecutor):
        """
        :param executor: An asynchronous executor
        """
        self.api_token = api_token
        self.executor = executor

    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        """
        Check if the users exist and retrieve their corresponding user_id and ssh_pubkey.
        """
        okta = model.okta
        session = async_retryable_session(self.executor)
        futures = []
        searcher = jmespath.compile("[*].[id, status, profile.sshPubKey] | [0]")
        for login in okta.users:
            future = session.get(f"https://{okta.organization}.okta.com/api/v1/users?limit=1&search=profile.login+eq+" +
                                 urllib.parse.quote(f'"{login}"'),
                                 headers=(self._http_headers()))
            futures.append(future)

        issues = []
        users_ext = {}
        logger.info(f"Checking Okta {okta.organization.capitalize()}'s Users:")
        login_max_len = max(map(len, okta.users), default=0)
        for login, future in zip(okta.users, futures):
            result = future.result()
            result.raise_for_status()
            json_response = json.loads(result.content.decode())
            match = searcher.search(json_response)
            user_data = {}
            if match:
                user_id, status, ssh_pubkey = match
                if status != "ACTIVE":
                    err_msg = f"status={status}"
                elif ssh_pubkey:
                    err_msg = None
                    user_data = {
                        "user_id": user_id,
                        "ssh_pubkey": ssh_pubkey,
                    }
                else:
                    status = "MISSING_SSH_PUBKEY"
                    err_msg = "Missing SSH PubKey"
            else:
                status = "ABSENT"
                err_msg = "Not found in OKTA"
            user_data["status"] = status
            if err_msg:
                color = "red"
                issues.append(Issue(level=IssueLevel.ERROR, type="USER", id=login, message=err_msg))
            else:
                color = "green"
            leader = "." * (2 + login_max_len - len(login))
            logger.opt(colors=True).info(f"  {login} {leader} <{color}>{status}</{color}>")
            users_ext[login] = user_data

        return Prodict(okta={"users": users_ext}), issues

    def _http_headers(self):
        return {
            'Accept': 'application/json',
            'Authorization': f'SSWS {self.api_token}'
        }
