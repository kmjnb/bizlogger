#!/bin/sh
set -eu

: "${VLESS_UUID:?VLESS_UUID required}"
: "${VLESS_HOST:?VLESS_HOST required}"
: "${VLESS_PORT:=443}"
: "${VLESS_SNI:=$VLESS_HOST}"
export VLESS_UUID VLESS_HOST VLESS_PORT VLESS_SNI

envsubst '${VLESS_UUID} ${VLESS_HOST} ${VLESS_PORT} ${VLESS_SNI}' \
  < /etc/xray/config.template.json \
  > /etc/xray/config.json

exec /usr/local/bin/xray run -c /etc/xray/config.json
