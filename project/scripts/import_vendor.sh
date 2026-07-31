#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_INPUT="${1:-$PROJECT_ROOT/vendor-input}"

if [[ "$VENDOR_INPUT" == "$PROJECT_ROOT/data"/* || "$VENDOR_INPUT" == "$PROJECT_ROOT/data" ]]; then
    printf 'ERROR: Vendor input must be outside %s/data\n' "$PROJECT_ROOT" >&2
    exit 2
fi

printf 'Compatibility wrapper: running the canonical staged rebuild.\n'
exec bash "$PROJECT_ROOT/scripts/rebuild_all.sh" \
    --vendor-input "$VENDOR_INPUT"
