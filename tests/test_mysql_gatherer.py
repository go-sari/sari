from concurrent.futures.thread import ThreadPoolExecutor

import pytest
from prodict import Prodict
from testcontainers.mysql import MySqlContainer

from main.gatherer.mysql import MySqlGatherer
from tests.test_gatherers import assert_dict_equals


@pytest.mark.slow
@pytest.mark.testcontainer
def test_mysql_gather_rds_status():
    db_name = "db_blackwells"
    username = "root"
    # noinspection HardcodedPassword
    password = "focused_mendel"
    # TODO: launch it at startup to use the same container to test rainy-day scenarios
    #  - connection refused
    #  - connection timeout
    #  - invalid username/password
    #  - invalid schema/database name
    #  - dropping dandling users
    with MySqlContainer("mysql:5.7.17",
                        MYSQL_DATABASE=db_name,
                        MYSQL_USER=username,
                        MYSQL_PASSWORD=password) as mysql:
        with ThreadPoolExecutor(max_workers=1) as executor:
            mysql_gatherer = MySqlGatherer(executor, None)
            model = Prodict(aws={
                "databases": {
                    "blackwells": {
                        "endpoint": {
                            "address": "localhost",
                            "port": mysql.get_exposed_port(3306),
                        },
                        "master_username": username,
                        "master_password": password,
                    }
                }
            })
            updates, issues = mysql_gatherer.gather(model)
    assert not issues
    assert_dict_equals(updates, {"aws": {"databases": {"blackwells": {"status": "ACCESSIBLE"}}}})
