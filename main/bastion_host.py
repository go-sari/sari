from typing import List

from paramiko.client import SSHClient, AutoAddPolicy
from paramiko.config import SSH_PORT


def update_authorized_keys(hostname: str,
                           admin_username: str,
                           key_filename,
                           passphrase: str,
                           username: str,
                           ssh_pub_keys: List[str],
                           port: int = None) -> List[str]:
    with SSHClient() as client:
        # noinspection ParamikoHostkeyBypass
        client.set_missing_host_key_policy(AutoAddPolicy)
        client.connect(hostname,
                       port=(port or SSH_PORT),
                       timeout=5,
                       banner_timeout=5,
                       username=admin_username,
                       passphrase=passphrase,
                       key_filename=key_filename)
        stdin, _, stderr = client.exec_command(f"sudo -u {username} tee ~{username}/.ssh/authorized_keys2")
        stdin.write("\n".join(ssh_pub_keys))
        stdin.close()
        return stderr.readlines()
