import json
import urllib.parse
from concurrent.futures.thread import ThreadPoolExecutor
from typing import List, Tuple

import jmespath
from loguru import logger
from prodict import Prodict
from requests import Session

from main.issue import Issue, IssueLevel
from main.rest import retryable_session, async_retryable_session


class OktaGatherer:

    def __init__(self, api_token, executor: ThreadPoolExecutor):
        """
        :param executor: An asynchronous executor
        """
        self.api_token = api_token
        self.executor = executor

    def gather_aws_app_info(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        okta = model.okta
        session: Session = retryable_session(Session())
        response = session.get(f"https://{okta.organization}.okta.com/api/v1/apps",
                               params={"filter": 'status eq "ACTIVE"'},
                               headers=(self._http_headers()))
        response.raise_for_status()
        json_response = json.loads(response.content.decode())
        app_id = jmespath.compile(f"[?label=='{okta.aws_app.label}'].id | [0]").search(json_response)
        if app_id:
            return Prodict(okta={"aws_app": {"app_id": app_id}}), []
        else:
            return Prodict(), [
                Issue(level=IssueLevel.CRITICAL, type="OKTA", id=okta.aws_app.label,
                      message="Okta application not found")
            ]

    def gather_user_info(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        """
        Check if the users exist and retrieve their corresponding user_id and ssh_pubkey.
        """
        okta = model.okta
        session = async_retryable_session(self.executor)
        futures = []
        searcher = jmespath.compile("[*].[id, profile.sshPubKey] | [0]")
        for login in okta.users:
            future = session.get(f"https://{okta.organization}.okta.com/api/v1/users?limit=1&search=profile.login+eq+" +
                                 urllib.parse.quote(f'"{login}"'),
                                 headers=(self._http_headers()))
            futures.append(future)

        # Additional query to list all assigned users and their SAML roles
        future = session.get(f"https://{okta.organization}.okta.com/api/v1/apps/{okta.aws_app.app_id}/users",
                             headers=(self._http_headers()))
        futures.append(future)

        issues = []
        users_ext = {}
        logger.info(f"Checking Okta {okta.organization.capitalize()}'s Users:")
        login_max_len = max(map(len, okta.users))
        for login, future in zip(okta.users, futures):
            result = future.result()
            result.raise_for_status()
            json_response = json.loads(result.content.decode())
            match = searcher.search(json_response)
            if match:
                user_id, ssh_pubkey = match
                if ssh_pubkey:
                    user_data = {
                        "status": "ACTIVE",
                        "user_id": user_id,
                        "ssh_pubkey": ssh_pubkey,
                    }
                    color = "green"
                else:
                    user_data = dict(status="INACTIVE")
                    issues.append(Issue(level=IssueLevel.ERROR, type="USER", id=login,
                                        message="Missing SSH PubKey"))
                    color = "light-magenta"
            else:
                user_data = dict(status="ABSENT")
                color = "red"
                issues.append(Issue(level=IssueLevel.ERROR, type="USER", id=login,
                                    message="Not found in OKTA"))
            leader = "." * (2 + login_max_len - len(login))
            logger.opt(colors=True).info(f"  {login} {leader} "
                                         f"<{color}>{user_data['status']}</{color}>")
            users_ext[login] = user_data

        result = futures[-1].result()
        result.raise_for_status()
        json_response = json.loads(result.content.decode())
        for entry in json_response:
            login = entry["externalId"]
            if login in users_ext:
                users_ext[login]["saml_roles"] = entry["profile"].get("samlRoles", [])

        return Prodict(okta={"users": users_ext}), issues

    def _http_headers(self):
        return {
            'Accept': 'application/json',
            'Authorization': f'SSWS {self.api_token}'
        }
