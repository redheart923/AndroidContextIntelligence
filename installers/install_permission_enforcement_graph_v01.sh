#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ $# -eq 0 ]]; then
    set -- --upgrade
fi
printf 'DEPRECATED: install_permission_enforcement_graph_v01.sh delegates to setup.sh\n' >&2
exec bash "$REPOSITORY_ROOT/setup.sh" "$@"
