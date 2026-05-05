#!/bin/sh
set -eu

: "${VLESS_UUID:?VLESS_UUID required}"
: "${VLESS_HOST:?VLESS_HOST required}"
: "${VLESS_PORT:=443}"
: "${VLESS_SNI:=$VLESS_HOST}"
: "${VLESS_PBK:?VLESS_PBK required (REALITY publicKey)}"
: "${VLESS_SID:?VLESS_SID required (REALITY shortId)}"
: "${VLESS_FP:=chrome}"
export VLESS_UUID VLESS_HOST VLESS_PORT VLESS_SNI VLESS_PBK VLESS_SID VLESS_FP

envsubst '${VLESS_UUID} ${VLESS_HOST} ${VLESS_PORT} ${VLESS_SNI} ${VLESS_PBK} ${VLESS_SID} ${VLESS_FP}' \
  < /etc/xray/config.template.json \
  > /etc/xray/config.json

exec xray run -c /etc/xray/config.json
