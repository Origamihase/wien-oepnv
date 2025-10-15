#!/usr/bin/env bash
set -euo pipefail

: "${VOR_ACCESS_ID:?VOR_ACCESS_ID muss gesetzt sein}"
: "${VOR_VERSIONS:?VOR_VERSIONS muss gesetzt sein}"

response=$(curl -sS "${VOR_VERSIONS}" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer ${VOR_ACCESS_ID}")

if command -v jq >/dev/null 2>&1; then
  jq '.' <<<"${response}"
else
  printf '%s\n' "${response}"
fi
