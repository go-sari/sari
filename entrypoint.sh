#!/bin/bash

set -eux

PULUMI_ACTION=$1

if [ -n "${PULUMI_BACKEND_URL:-}" ]; then
    pulumi --non-interactive login --cloud-url $PULUMI_BACKEND_URL
else
    pulumi --non-interactive login --local
fi

pulumi --non-interactive stack select this --create

pulumi --non-interactive ${PULUMI_ACTION:-preview}
