#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONFIG="$PROJECT_ROOT/config/source_roots.default.toml"
LOCAL_CONFIG="$PROJECT_ROOT/config/source_roots.local.toml"
REGISTRY="$PROJECT_ROOT/config/parser_registry.toml"
BUILD_INPUTS_DEFAULT="$PROJECT_ROOT/config/build_inputs.default.toml"
BUILD_INPUTS_LOCAL="$PROJECT_ROOT/config/build_inputs.local.toml"
if [[ -f "$BUILD_INPUTS_LOCAL" ]]; then
    BUILD_INPUTS="$BUILD_INPUTS_LOCAL"
else
    BUILD_INPUTS="$BUILD_INPUTS_DEFAULT"
fi
VENDOR_INPUT="$PROJECT_ROOT/vendor-input"
VENDOR_CACHE="$PROJECT_ROOT/.cache/vendor-artifacts"
SERVICE_CACHE="$PROJECT_ROOT/.cache/service-registration"
CODEQL_CACHE="$PROJECT_ROOT/.cache/codeql-results"
CODEQL_DB=""
CODEQL_BIN="${CODEQL_BIN:-codeql}"
CORRECTIONS_DIR="$PROJECT_ROOT/config/corrections"
RETAIN_HISTORY=0
RETAIN_HISTORY_DATABASE=0
CALL_DATAFLOW_STRICT=0
JADX_BIN="${JADX_BIN:-jadx}"
MODE="rebuild"
KEEP_FAILED=0
STRICT=()
NATIVE_STRICT=()
PROVENANCE_STRICT=()

usage() {
    cat <<'EOF'
Usage: rebuild_all.sh [OPTIONS]

Options:
  --source-config FILE        Use an alternate source-roots configuration.
  --local-config FILE         Use an alternate local override configuration.
  --discover-only             Refresh workspace discovery reports only.
  --plan-only                 Refresh the execution plan only.
  --strict                    Fail on every unsupported detected capability.
  --strict-capability NAME    Fail when NAME lacks parser coverage.
  --vendor-input DIR          Read Vendor APK/JAR inputs outside data/.
  --vendor-cache DIR          Use a content-addressed decompilation cache.
  --jadx-bin FILE             Use a specific JADX executable.
  --codeql-db DIR             Import a verified java-kotlin CodeQL database.
  --codeql-bin FILE           Use a specific CodeQL executable.
  --build-inputs FILE         Import optional Ninja/build metadata configuration.
  --corrections-dir DIR       Replay approved Git-managed corrections.
  --retain-history            Retain immutable reports for the verified build.
  --retain-history-database   Also retain the verified SQLite database.
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
        --local-config)
            [[ $# -ge 2 ]] || die "--local-config requires a path"
            LOCAL_CONFIG="$2"
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
            PROVENANCE_STRICT+=(--require-complete)
            CALL_DATAFLOW_STRICT=1
            for capability in native_symbols native_types native_includes rust_ffi jni_bindings soong_build_graph; do
                NATIVE_STRICT+=(--strict-capability "$capability")
            done
            shift
            ;;
        --strict-capability)
            [[ $# -ge 2 ]] || die "--strict-capability requires a name"
            STRICT+=(--strict-capability "$2")
            PROVENANCE_STRICT+=(--require-complete)
            if [[ "$2" == "call_graph" || "$2" == "interprocedural_dataflow" ]]; then
                CALL_DATAFLOW_STRICT=1
            fi
            case "$2" in
                native_symbols|native_types|native_includes|rust_ffi|jni_bindings|soong_build_graph|ninja_build_graph)
                    NATIVE_STRICT+=(--strict-capability "$2")
                    ;;
            esac
            shift 2
            ;;
        --build-inputs)
            [[ $# -ge 2 ]] || die "--build-inputs requires a path"
            BUILD_INPUTS="$2"
            shift 2
            ;;
        --codeql-db)
            [[ $# -ge 2 ]] || die "--codeql-db requires a path"
            CODEQL_DB="$2"
            shift 2
            ;;
        --codeql-bin)
            [[ $# -ge 2 ]] || die "--codeql-bin requires a path"
            CODEQL_BIN="$2"
            shift 2
            ;;
        --corrections-dir)
            [[ $# -ge 2 ]] || die "--corrections-dir requires a path"
            CORRECTIONS_DIR="$2"
            shift 2
            ;;
        --retain-history)
            RETAIN_HISTORY=1
            shift
            ;;
        --retain-history-database)
            RETAIN_HISTORY=1
            RETAIN_HISTORY_DATABASE=1
            shift
            ;;
        --keep-failed-db)
            KEEP_FAILED=1
            shift
            ;;
        --vendor-input)
            [[ $# -ge 2 ]] || die "--vendor-input requires a path"
            VENDOR_INPUT="$2"
            shift 2
            ;;
        --vendor-cache)
            [[ $# -ge 2 ]] || die "--vendor-cache requires a path"
            VENDOR_CACHE="$2"
            shift 2
            ;;
        --jadx-bin)
            [[ $# -ge 2 ]] || die "--jadx-bin requires a path"
            JADX_BIN="$2"
            shift 2
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
        --local-config "$LOCAL_CONFIG" \
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
VENDOR_MANIFEST="$STAGED_WORKSPACE/vendor-artifacts.json"
SCOPE_REPORT="$STAGED_WORKSPACE/source-scope-validation.json"
SOURCE_PROVENANCE="$STAGED_WORKSPACE/source-provenance.json"

python -m workspace.cli \
    --config "$SOURCE_CONFIG" \
    --local-config "$LOCAL_CONFIG" \
    --registry "$REGISTRY" \
    --out-dir "$STAGED_WORKSPACE" \
    "${STRICT[@]}"

python -m workspace.source_scope_validation preflight \
    --plan "$PLAN" \
    --output "$SCOPE_REPORT"

sqlite3 "$STAGED_DB" < "$PROJECT_ROOT/storage/schema.sql"

python -m workspace.schema_migrations \
    --db "$STAGED_DB" \
    --migrations "$PROJECT_ROOT/storage/migrations"

python -m workspace.vendor_artifacts prepare \
    --input-dir "$VENDOR_INPUT" \
    --cache-dir "$VENDOR_CACHE" \
    --jadx "$JADX_BIN" \
    --data-root "$PROJECT_ROOT/data" \
    --report "$VENDOR_MANIFEST"

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

python -m workspace.multi_vendor \
    --manifest "$VENDOR_MANIFEST" \
    --db "$STAGED_DB" \
    --staging-root "$STAGING" \
    --ctags-dir "$STAGED_RAW/vendor" \
    --report "$STAGED_RAW/vendor/vendor-import-report.json"

python -m workspace.multi_service \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --cache-dir "$SERVICE_CACHE" \
    --report "$STAGED_RAW/service/service-registration-report.json"

python -m workspace.multi_permission \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --report "$STAGED_RAW/permission/permission-semantics-report.json"

python -m workspace.permission_validation \
    --db "$STAGED_DB" \
    --report "$STAGED_RAW/permission/permission-semantics-report.json"

python -m workspace.pipeline annotate \
    --plan "$PLAN" \
    --db "$STAGED_DB"

NATIVE_PIPELINE_REPORT="$STAGED_RAW/native-pipeline-report.json"
python -m workspace.native_pipeline \
    --db "$STAGED_DB" \
    --plan "$PLAN" \
    --raw-root "$STAGED_RAW" \
    --build-inputs "$BUILD_INPUTS" \
    "${NATIVE_STRICT[@]}"

CODEQL_REPORT="$STAGED_RAW/codeql/call-dataflow-report.json"
CORRECTION_REPORT="$STAGED_WORKSPACE/correction-application-report.json"
CALL_DATAFLOW_REPORT="$STAGED_WORKSPACE/call-dataflow-validation.json"

if [[ -z "$CODEQL_DB" ]]; then
    python -m workspace.codeql_import \
        --skip \
        --report "$CODEQL_REPORT" \
        --correction-report "$CORRECTION_REPORT"
    [[ "$CALL_DATAFLOW_STRICT" -eq 0 ]] || \
        die "strict call/dataflow capability requires --codeql-db"
else
    [[ -d "$CODEQL_DB" ]] || die "CodeQL database is not a directory: $CODEQL_DB"
    if [[ "$CODEQL_BIN" != */* ]]; then
        CODEQL_BIN="$(command -v "$CODEQL_BIN" || true)"
    fi
    [[ -x "$CODEQL_BIN" ]] || die "CodeQL executable is unavailable: $CODEQL_BIN"
    python -m workspace.codeql_import \
        --db "$STAGED_DB" \
        --codeql-db "$CODEQL_DB" \
        --codeql-bin "$CODEQL_BIN" \
        --pack "$PROJECT_ROOT/codeql" \
        --cache-dir "$CODEQL_CACHE" \
        --plan "$PLAN" \
        --corrections-dir "$CORRECTIONS_DIR" \
        --report "$CODEQL_REPORT" \
        --correction-report "$CORRECTION_REPORT"
fi

python -m workspace.correction_replay \
    --db "$STAGED_DB" \
    --corrections-dir "$CORRECTIONS_DIR" \
    --report "$CORRECTION_REPORT"

CALL_DATAFLOW_VALIDATION_ARGS=()
if [[ "$CALL_DATAFLOW_STRICT" -eq 1 ]]; then
    CALL_DATAFLOW_VALIDATION_ARGS+=(--require-aosp-evidence)
fi
python -m workspace.call_dataflow_validation \
    --db "$STAGED_DB" \
    --config "$PROJECT_ROOT/config/codeql.toml" \
    --report "$CALL_DATAFLOW_REPORT" \
    "${CALL_DATAFLOW_VALIDATION_ARGS[@]}"

python -m workspace.symbol_collision_validation \
    --db "$STAGED_DB" \
    --plan "$PLAN" \
    --report "$STAGED_WORKSPACE/symbol-collisions.json"

python -m workspace.coverage_validation \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --report "$STAGED_WORKSPACE/capability-report.json"

python "$PROJECT_ROOT/scripts/graph_fingerprint.py" \
    --db "$STAGED_DB" \
    --format json \
    > "$STAGED_WORKSPACE/semantic-fingerprints.json"

python -m workspace.provenance collect \
    --plan "$PLAN" \
    --source-config "$SOURCE_CONFIG" \
    --local-config "$LOCAL_CONFIG" \
    --registry "$REGISTRY" \
    --vendor-manifest "$VENDOR_MANIFEST" \
    --codeql-report "$CODEQL_REPORT" \
    --correction-report "$CORRECTION_REPORT" \
    --build-inputs "$BUILD_INPUTS" \
    --native-pipeline-report "$NATIVE_PIPELINE_REPORT" \
    --fingerprints "$STAGED_WORKSPACE/semantic-fingerprints.json" \
    --output "$SOURCE_PROVENANCE"

python -m workspace.provenance validate \
    --provenance "$SOURCE_PROVENANCE" \
    "${PROVENANCE_STRICT[@]}"

python -m workspace.source_scope_validation post-import \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --capability-report "$STAGED_WORKSPACE/capability-report.json" \
    --provenance "$SOURCE_PROVENANCE" \
    --build-id "$(basename "$STAGING")" \
    --output "$SCOPE_REPORT"

python -m workspace.source_scope_validation bind-fingerprint \
    --fingerprints "$STAGED_WORKSPACE/semantic-fingerprints.json" \
    --scope-report "$SCOPE_REPORT"

python -m workspace.provenance collect \
    --plan "$PLAN" \
    --source-config "$SOURCE_CONFIG" \
    --local-config "$LOCAL_CONFIG" \
    --registry "$REGISTRY" \
    --vendor-manifest "$VENDOR_MANIFEST" \
    --codeql-report "$CODEQL_REPORT" \
    --correction-report "$CORRECTION_REPORT" \
    --build-inputs "$BUILD_INPUTS" \
    --native-pipeline-report "$NATIVE_PIPELINE_REPORT" \
    --fingerprints "$STAGED_WORKSPACE/semantic-fingerprints.json" \
    --scope-report "$SCOPE_REPORT" \
    --output "$STAGED_WORKSPACE/provenance.json"

python -m workspace.provenance validate \
    --provenance "$STAGED_WORKSPACE/provenance.json" \
    "${PROVENANCE_STRICT[@]}"

FK_ERRORS="$(sqlite3 "$STAGED_DB" 'PRAGMA foreign_key_check;')"
if [[ -n "$FK_ERRORS" ]]; then
    printf '%s\n' "$FK_ERRORS" >&2
    die "foreign_key_check failed"
fi
printf 'foreign_key_check: PASS\n'

ANALYSIS_SCOPE="$(
    python -c \
        'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["analysis_scope"])' \
        "$SCOPE_REPORT"
)"
if [[ "$ANALYSIS_SCOPE" == "aosp" ]]; then
    [[ -f "$PROJECT_ROOT/queries/ams_service_chain.sql" ]] &&
        sqlite3 -header -column "$STAGED_DB" \
            < "$PROJECT_ROOT/queries/ams_service_chain.sql"
    [[ -f "$PROJECT_ROOT/queries/pms_service_chain.sql" ]] &&
        sqlite3 -header -column "$STAGED_DB" \
            < "$PROJECT_ROOT/queries/pms_service_chain.sql"
fi

VERIFIED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
HISTORY_ARGS=()
if [[ "$RETAIN_HISTORY" -eq 1 ]]; then
    HISTORY_ARGS+=(--retain-history)
fi
if [[ "$RETAIN_HISTORY_DATABASE" -eq 1 ]]; then
    HISTORY_ARGS+=(--retain-history-database)
fi
python -m workspace.build_publish prepare \
    --staging "$STAGING" \
    --source-config "$SOURCE_CONFIG" \
    --local-config "$LOCAL_CONFIG" \
    --provenance "$STAGED_WORKSPACE/provenance.json" \
    --vendor-manifest "$VENDOR_MANIFEST" \
    --scope-report "$SCOPE_REPORT" \
    --started-at "$STARTED_AT" \
    --verified-at "$VERIFIED_AT" \
    "${HISTORY_ARGS[@]}"

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
