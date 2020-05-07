#!/bin/sh

set -eux

PULUMI_ACTION=$1

if [ -n "$PULUMI_BACKEND_URL" ]; then
    pulumi login --cloud-url $PULUMI_BACKEND_URL
else
    pulumi login --local
fi

[ -f Pulumi.this.yaml ] || pulumi stack init this
if [ -n "$SOCKS5_PROXY" ]; then
    pulumi config set socks5_proxy $SOCKS5_PROXY
fi
pulumi $PULUMI_ACTION
