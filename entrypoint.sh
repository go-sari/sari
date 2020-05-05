#!/bin/sh

set -eux

PULUMI_ACTION=$1

pulumi login --local
pulumi stack init this
if [ -n "$SOCKS5_PROXY" ]; then
    pulumi config set socks5_proxy $SOCKS5_PROXY
fi
pulumi $PULUMI_ACTION
