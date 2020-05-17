import socket
import textwrap
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Tuple, List, Optional
from urllib.parse import urlparse

import mysql.connector
# noinspection PyPackageRequirements
import socks
from loguru import logger
from prodict import Prodict

from main.constants import MYSQL_LOGIN_TIMEOUT, MYSQL_CONNECT_TIMEOUT
from main.dbstatus import DbStatus
from main.issue import Issue, IssueLevel


class MySqlGatherer:

    def __init__(self, executor: ThreadPoolExecutor, proxy: Optional[str]):
        self.executor = executor
        self.proxy = proxy

    def gather_rds_status(self, model: Prodict) -> Tuple[Prodict, List[Issue]]:
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
                success, message, extra_message = future.result(MYSQL_LOGIN_TIMEOUT)
                color = ("red", "green")[success]
                if success:
                    updates[db_id] = accessible
                else:
                    issues.append(Issue(level=IssueLevel.ERROR, type="DB", id=db_id,
                                        message=message))
            else:
                success, message, extra_message = (False, databases[db_id].status, None)
                color = "light-magenta"
            leader = "." * (2 + db_id_max_len - len(db_id))
            logger.opt(colors=True).info(f"  {db_id} {leader} <{color}>{message}</{color}>")
            if extra_message:
                color = "light-yellow"
                logger.opt(colors=True).info(f"  {' ' * (3 + db_id_max_len)} <{color}>{extra_message}</{color}>")
        return Prodict(aws={"databases": updates}), issues


def _check_mysql_instance(db) -> Tuple[bool, str, Optional[str]]:
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
                                             password=db.plain_master_password,
                                             connection_timeout=MYSQL_CONNECT_TIMEOUT,
                                             # Only Pure Python connector implementation supports SOCKS5
                                             use_pure=True)
        db_info = connection.get_server_info()
        extra_msg = _drop_dangling_users(db, connection)
        return True, f'OK ("MySQL Server version {db_info}")', extra_msg
    except Exception as e:
        return False, f'ERROR: {str(e)}', None
    finally:
        if connection and connection.is_connected():
            connection.commit()
            connection.close()


def _drop_dangling_users(db, connection) -> Optional[str]:
    query = connection.cursor()
    try:
        query.execute(textwrap.dedent("""
                SELECT user,
                       host
                  FROM user
                 WHERE POSITION('@' IN user) > 0
            """))
        managed_users = db.managed_users or set()
        users_to_drop = {f"'{user}'@'{host}'" for user, host in query.fetchall()
                         if user not in managed_users}
    finally:
        query.close()
    msg = None
    if users_to_drop:
        update = connection.cursor()
        try:
            stmt = f"DROP USER {', '.join(users_to_drop)}"
            update.execute(stmt)
            msg = stmt
        finally:
            update.close()
    return msg


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
