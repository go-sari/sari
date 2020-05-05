import socket
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Tuple, List

import mysql.connector
# noinspection PyPackageRequirements
import socks
from loguru import logger
from prodict import Prodict

from main.constants import MYSQL_LOGIN_TIMEOUT, MYSQL_CONNECT_TIMEOUT
from main.dbstatus import DbStatus
from main.issue import Issue, IssueLevel


class MySqlGatherer:

    def __init__(self, executor: ThreadPoolExecutor, socks5_proxy: Tuple[str, int]):
        self.executor = executor
        self.socks5_proxy = socks5_proxy

    def gather_rds_status(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
        with _Socks5ProxyContext(self.socks5_proxy):
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
        for db_id, db in databases.items():
            if db.endpoint:
                future = self.executor.submit(_check_mysql_instance, db)
            else:
                future = None
            futures.append(future)
        db_id_max_len = max(map(len, databases))
        updates = {}
        accessible = dict(status=DbStatus.ACCESSIBLE.name)
        for db_id, future in zip(databases, futures):
            if future:
                success, message = future.result(MYSQL_LOGIN_TIMEOUT)
                color = ("red", "green")[success]
                if success:
                    updates[db_id] = accessible
                else:
                    issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_id,
                                        message=message))
            else:
                success, message = (False, databases[db_id].status)
                color = "light-magenta"
            leader = "." * (2 + db_id_max_len - len(db_id))
            logger.opt(colors=True).info(f"  {db_id} {leader} <{color}>{message}</{color}>")
        return Prodict(aws={"databases": updates}), issues


def _check_mysql_instance(db):
    """For a particular RDS instances: check if it's possible to connect, authenticate with credentials,
    and get authorized access to the primary DB.

    :rtype: (bool, str)
    :returns: if the check succeeded, returns **True** and the server version string.
    Otherwise, returns **False** and the corresponding error message.
    """
    connection = None
    try:
        connection = mysql.connector.connect(host=db.endpoint.address,
                                             port=db.endpoint.port,
                                             ssl_disabled=True,
                                             database=db.db_name,
                                             user=db.master_username,
                                             password=db.plain_master_password,
                                             connection_timeout=MYSQL_CONNECT_TIMEOUT,
                                             # Only Pure Python connector implementation supports SOCKS5
                                             use_pure=True)
        db_info = connection.get_server_info()
        return True, f'OK ("MySQL Server version {db_info}")'
    except Exception as e:
        return False, f'ERROR: {str(e)}'
    finally:
        if connection and connection.is_connected():
            connection.close()


class _Socks5ProxyContext:
    def __init__(self, socks5_proxy: Tuple[str, int]):
        self.socks5_proxy = socks5_proxy
        self.old_socket = None

    def __enter__(self):
        self.old_socket = socket.socket
        if self.socks5_proxy:
            # Monkeypatch
            socks.set_default_proxy(socks.SOCKS5, self.socks5_proxy[0], self.socks5_proxy[1])
            socket.socket = socks.socksocket

    def __exit__(self, exc_type, exc_val, exc_tb):
        socket.socket = self.old_socket
