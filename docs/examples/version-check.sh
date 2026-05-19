#!/usr/bin/env bash
# Inspect the VAO ReST API metadata for the currently configured project version.
#
# Background:
#   VOR_VERSION (and the deprecated alias VOR_VERSIONS) is a *version string*
#   (e.g. "v1.11.0") that the project embeds into VOR_BASE_URL — it is NOT a
#   URL to a "/versions" endpoint (the VAO API does not expose one). The
#   canonical way to verify which version the API is actually serving is to
#   query the /datainfo endpoint, whose Operator/Product/ProductCategory
#   payload changes between releases.
#
# Usage:
#   VOR_ACCESS_ID=<token> VOR_BASE_URL=https://routenplaner.../v1.11.0/ \
#     bash docs/examples/version-check.sh
#
# VOR_BASE_URL is expected to include a trailing slash, mirroring
# src/providers/vor.py:DEFAULT_BASE_URL.

set -euo pipefail

: "${VOR_ACCESS_ID:?VOR_ACCESS_ID muss gesetzt sein}"
: "${VOR_BASE_URL:?VOR_BASE_URL muss gesetzt sein (inkl. Versions-Pfad und Trailing-Slash)}"

response=$(curl -sS -G "${VOR_BASE_URL}datainfo" \
  --data-urlencode "accessId=${VOR_ACCESS_ID}" \
  -H "Accept: application/json")

if command -v jq >/dev/null 2>&1; then
  jq '.' <<<"${response}"
else
  printf '%s\n' "${response}"
fi
