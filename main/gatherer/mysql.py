import socket
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Tuple, List, Optional
from urllib.parse import urlparse

import mysql.connector
# noinspection PyPackageRequirements
import socks
from loguru import logger
from prodict import Prodict

from main.dbstatus import DbStatus
from main.issue import Issue, IssueLevel
from .gatherer import Gatherer

MYSQL_CONNECT_TIMEOUT = 4
MYSQL_LOGIN_TIMEOUT = 10


class MySqlGatherer(Gatherer):

    def __init__(self, executor: ThreadPoolExecutor, proxy: Optional[str]):
        self.executor = executor
        self.proxy = proxy

    def gather(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        with _ProxyContext(self.proxy):
            return self._gather_rds_status(model)

    def _gather_rds_status(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        """
        For all RDS instances: check if it's possible to connect, authenticate with credentials, and get authorized
         access to the primary DB. Reports each instance check on the console.
        """

        databases = model.aws.databases
        logger.info("Checking access to RDS instances:")

        issues = []
        futures = []
        for db_uid, db in databases.items():
            if db.endpoint:
                future = self.executor.submit(_check_mysql_instance, db)
            else:
                future = None
            futures.append(future)
        db_id_max_len = max(map(len, databases))
        updates = {}
        accessible = dict(status=DbStatus.ACCESSIBLE.name)
        for db_uid, future in zip(databases, futures):
            if future:
                success, message = future.result(MYSQL_LOGIN_TIMEOUT)
                color = ("red", "green")[success]
                if success:
                    updates[db_uid] = accessible
                else:
                    issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_uid,
                                        message=message))
            else:
                success, message = (False, databases[db_uid].status)
                color = "light-magenta"
            leader = "." * (2 + db_id_max_len - len(db_uid))
            logger.opt(colors=True).info(f"  {db_uid} {leader} <{color}>{message}</{color}>")
        return Prodict(aws={"databases": updates}), issues


def _check_mysql_instance(db) -> Tuple[bool, str]:
    """For a particular RDS instances: check if it's possible to connect, authenticate with credentials,
    and get authorized access to the primary DB.

    :returns: if the check succeeded, returns **True** and the server version string.
    Otherwise, returns **False** and the corresponding error message.
    """
    connection = None
    try:
        connection = mysql.connector.connect(host=db.endpoint.address,
                                             port=db.endpoint.port,
                                             ssl_disabled=True,
                                             database="mysql",
                                             user=db.master_username,
                                             password=db.master_password,
                                             connection_timeout=MYSQL_CONNECT_TIMEOUT,
                                             # Only Pure Python connector implementation supports SOCKS5
                                             use_pure=True)
        db_info = connection.get_server_info()
        return True, f'OK ("MySQL Server version {db_info}")'
    except Exception as e:
        return False, f'ERROR: {str(e)}'
    finally:
        if connection and connection.is_connected():
            connection.commit()
            connection.close()


class _ProxyContext:
    def __init__(self, proxy: Optional[str]):
        self.proxy = proxy
        self.old_socket = None

    def __enter__(self):
        self.old_socket = socket.socket
        if self.proxy:
            # Monkeypatch
            parts = urlparse(self.proxy)
            proxy_type = socks.PROXY_TYPES[parts.scheme.upper()]
            socks.set_default_proxy(proxy_type, parts.hostname, parts.port)
            socket.socket = socks.socksocket

    def __exit__(self, exc_type, exc_val, exc_tb):
        socket.socket = self.old_socket
