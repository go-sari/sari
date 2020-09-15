import pytest
from paramiko.config import SSH_PORT
from testcontainers.core.container import DockerContainer

from main.updater.ssh import update_authorized_keys


@pytest.mark.slow
@pytest.mark.flaky(reruns=4)
@pytest.mark.testcontainer
def test_update_authorized_keys():
    with DockerContainer("quay.io/eliezio/sari-test-bh:v1.1.0").with_exposed_ports(SSH_PORT) as server:
        ssh_pub_keys = [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEfzjdkO1LKnS/it62jmw9tH4BznlnDCBrzaKguujJ15 "
            "leroy.trent@acme.com",
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGD13Dbe1QoYrFZqCue1TzGkzDSra9ZHzv8gZy9+vb0Y "
            "bridget.huntington-whiteley@acme.com",
        ]
        errors = update_authorized_keys(server.get_container_host_ip(), "admin",
                                        "tests/data/admin_id_rsa", "",
                                        "acme", ssh_pub_keys,
                                        int(server.get_exposed_port(SSH_PORT)))
        assert not errors
        # noinspection PyProtectedMember
        _, stat = server._container.get_archive("/home/acme/.ssh/authorized_keys2")
        assert stat["size"] == sum(map(len, ssh_pub_keys)) + (len(ssh_pub_keys) - 1)
