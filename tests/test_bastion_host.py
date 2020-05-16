import mockssh
import pytest

from main.bastion_host import update_authorized_keys


@pytest.mark.slow
def test_update_authorized_keys():
    admin_user = ("admin", "tests/data/admin_id_rsa")
    users = dict([admin_user])
    with mockssh.Server(users) as server:
        update_authorized_keys(server.host,
                               admin_user[0], admin_user[1], "", "acme", [
                                   "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEfzjdkO1LKnS/it62jmw9tH4BznlnDCBrzaKguujJ15 "
                                   "leroy.trent@acme.com",
                                   "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGD13Dbe1QoYrFZqCue1TzGkzDSra9ZHzv8gZy9+vb0Y "
                                   "bridget.huntington-whiteley@acme.com",
                               ],
                               server.port)
    # TODO: missing validation!
