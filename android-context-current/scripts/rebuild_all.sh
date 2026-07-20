#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONFIG="$PROJECT_ROOT/config/source_roots.toml"
REGISTRY="$PROJECT_ROOT/config/parser_registry.toml"
MODE="rebuild"
KEEP_FAILED=0
STRICT=()

usage() {
    cat <<'EOF'
Usage: rebuild_all.sh [OPTIONS]

Options:
  --source-config FILE        Use an alternate source-roots configuration.
  --discover-only             Refresh workspace discovery reports only.
  --plan-only                 Refresh the execution plan only.
  --strict                    Fail on every unsupported detected capability.
  --strict-capability NAME    Fail when NAME lacks parser coverage.
  --keep-failed-db            Retain the complete failed staging batch.
  -h, --help                  Show this help.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-config)
            [[ $# -ge 2 ]] || die "--source-config requires a path"
            SOURCE_CONFIG="$2"
            shift 2
            ;;
        --discover-only)
            MODE="discover"
            shift
            ;;
        --plan-only)
            MODE="plan"
            shift
            ;;
        --strict)
            STRICT+=(--strict)
            shift
            ;;
        --strict-capability)
            [[ $# -ge 2 ]] || die "--strict-capability requires a name"
            STRICT+=(--strict-capability "$2")
            shift 2
            ;;
        --keep-failed-db)
            KEEP_FAILED=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

mkdir -p "$PROJECT_ROOT/data"
exec 9>"$PROJECT_ROOT/data/.rebuild.lock"
flock -n 9 || die "another rebuild is already running"

python -m workspace.build_publish recover \
    --data-root "$PROJECT_ROOT/data"

if [[ "$MODE" == "discover" || "$MODE" == "plan" ]]; then
    python -m workspace.cli \
        --config "$SOURCE_CONFIG" \
        --registry "$REGISTRY" \
        --out-dir "$PROJECT_ROOT/data/workspace" \
        "${STRICT[@]}"
    exit 0
fi

STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
STAGING=""
PUBLISHED=0

cleanup_failed_batch() {
    local status=$?
    trap - EXIT INT TERM
    if [[ "$PUBLISHED" -eq 0 && -n "$STAGING" && -d "$STAGING" ]]; then
        if [[ "$KEEP_FAILED" -eq 1 ]]; then
            python -m workspace.build_publish fail \
                --staging "$STAGING" \
                --keep || true
        else
            python -m workspace.build_publish fail \
                --staging "$STAGING" || true
        fi
    fi
    exit "$status"
}

trap cleanup_failed_batch EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STAGING="$(
    python -m workspace.build_publish begin \
        --data-root "$PROJECT_ROOT/data"
)"
STAGED_DB="$STAGING/android_context.db"
STAGED_WORKSPACE="$STAGING/workspace"
STAGED_RAW="$STAGING/raw"
PLAN="$STAGED_WORKSPACE/execution-plan.json"

python -m workspace.cli \
    --config "$SOURCE_CONFIG" \
    --registry "$REGISTRY" \
    --out-dir "$STAGED_WORKSPACE" \
    "${STRICT[@]}"

sqlite3 "$STAGED_DB" < "$PROJECT_ROOT/storage/schema.sql"

python -m workspace.pipeline java \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --ctags-dir "$STAGED_RAW/ctags"

python -m workspace.pipeline kotlin \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --ctags-dir "$STAGED_RAW/ctags"

python -m workspace.multi_aidl \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --report "$STAGED_RAW/aidl/aidl-binder-report.json"

python -m workspace.pipeline inheritance \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --ctags-dir "$STAGED_RAW/ctags" \
    --report-dir "$STAGED_RAW/inheritance"

python -m workspace.multi_service \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --report "$STAGED_RAW/service/service-registration-report.json"

python -m workspace.multi_permission \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --report "$STAGED_RAW/permission/permission-enforcement-report.json"

python -m workspace.pipeline annotate \
    --plan "$PLAN" \
    --db "$STAGED_DB"

FK_ERRORS="$(sqlite3 "$STAGED_DB" 'PRAGMA foreign_key_check;')"
if [[ -n "$FK_ERRORS" ]]; then
    printf '%s\n' "$FK_ERRORS" >&2
    die "foreign_key_check failed"
fi
printf 'foreign_key_check: PASS\n'

LOCAL_SERVICE_COUNT="$(
    sqlite3 "$STAGED_DB" \
        "SELECT COUNT(*) FROM edge WHERE edge_type='EXPOSED_AS_LOCAL_SERVICE';"
)"
[[ "$LOCAL_SERVICE_COUNT" -ge 1 ]] || die "LocalServices validation failed"

[[ -f "$PROJECT_ROOT/queries/ams_service_chain.sql" ]] &&
    sqlite3 -header -column "$STAGED_DB" \
        < "$PROJECT_ROOT/queries/ams_service_chain.sql"
[[ -f "$PROJECT_ROOT/queries/pms_service_chain.sql" ]] &&
    sqlite3 -header -column "$STAGED_DB" \
        < "$PROJECT_ROOT/queries/pms_service_chain.sql"

VERIFIED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
python -m workspace.build_publish prepare \
    --staging "$STAGING" \
    --source-config "$SOURCE_CONFIG" \
    --started-at "$STARTED_AT" \
    --verified-at "$VERIFIED_AT"

python -m workspace.build_publish publish \
    --staging "$STAGING"

PUBLISHED=1
trap - EXIT INT TERM

printf 'Workspace coverage:\n'
python - "$PROJECT_ROOT/data/workspace/capability-report.json" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key, value in sorted(Counter(item["status"] for item in items).items()):
    print(f"  {key}: {value}")
PY
