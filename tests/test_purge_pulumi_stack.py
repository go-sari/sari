import json
from pathlib import Path

from main.util import assert_dict_equals, purge_pulumi_stack


def test_purge_acme_pulumi_stack():
    rds_known_endpoints = {"blackwells.c36k3kl10p4v.eu-west-1.rds.amazonaws.com:3306"}
    original_stack = json.loads(Path("tests/data/stk-acme.json").read_text())
    purged_stack = json.loads(Path("tests/data/stk-acme-purged.json").read_text())
    stack, num_changes = purge_pulumi_stack(original_stack, rds_known_endpoints)
    assert num_changes == 7
    assert_dict_equals(stack, purged_stack)
