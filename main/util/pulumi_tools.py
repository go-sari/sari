import copy
from typing import Set, Tuple


def purge_pulumi_stack(original_stack: dict, live_rds_endpoints: Set[str]) -> Tuple[dict, int]:
    """
    Purge resources whose providers were deleted without prior knowledge.

    :param original_stack: The original Pulumi Stack.
    :param live_rds_endpoints: The RDS endpoints that are still valid.
    :return: The purged stack and the number of resources effectively purged.
    """

    def is_zombie_provider(resource) -> bool:
        return resource["type"] == "pulumi:providers:mysql" and \
               resource["inputs"]["endpoint"] not in live_rds_endpoints

    stack = copy.deepcopy(original_stack)
    zombie_providers = set()
    live_resources = []
    resources = stack["deployment"]["resources"]
    for res in resources:
        if is_zombie_provider(res):
            provider_canonical_reference = f"{res['urn']}::{res['id']}"
            zombie_providers.add(provider_canonical_reference)
        elif res.get("provider") not in zombie_providers:
            live_resources.append(res)
    stack["deployment"]["resources"] = live_resources
    return stack, len(resources) - len(live_resources)
