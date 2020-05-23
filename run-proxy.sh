#!/bin/bash
# shellcheck disable=SC2086

set -eu

TMP_KEY_FILENAME=$(mktemp)
trap "rm -f $TMP_KEY_FILENAME" EXIT

if [ -n "${BH_ADMIN_PRIVATE_KEY:-}" ]; then
    echo $BH_ADMIN_PRIVATE_KEY | sed -e 's/ -----/\n-----/' | fmt -w64 -s > $TMP_KEY_FILENAME
else
    cp $BH_ADMIN_KEY_FILENAME $TMP_KEY_FILENAME
fi
ssh-keygen -p -f $TMP_KEY_FILENAME -P $BH_ADMIN_KEY_PASSPHRASE -N '' > /dev/null
ssh -fN \
    -4 -D 1080 \
    -i $TMP_KEY_FILENAME \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    $BH_PROXY_USERNAME@$BH_HOSTNAME
echo PROXY=socks5://localhost:1080
