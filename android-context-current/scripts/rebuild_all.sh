#!/usr/bin/env bash
set -Eeuo pipefail

AOSP_ROOT="${AOSP_ROOT:-/home/ts/aosp}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/ts/android-context-intelligence}"
FW_BASE="$AOSP_ROOT/frameworks/base"
DB_PATH="$PROJECT_ROOT/data/android_context.db"
CTAGS_OUTPUT="$PROJECT_ROOT/data/raw/ctags/frameworks-base.jsonl"

log() {
    printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"
}

die() {
    printf '\n[ERROR] %s\n' "$*" >&2
    exit 1
}

trap 'die "failed at line $LINENO"' ERR

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

scan_paths=(
    "$FW_BASE/core"
    "$FW_BASE/services"
)
[[ -d "$FW_BASE/packages" ]] && scan_paths+=("$FW_BASE/packages")

log "Running unit tests"
pytest -q

log "Collecting Universal Ctags JSON"
rm -f "$CTAGS_OUTPUT"
ctags \
    --languages=Java \
    --output-format=json \
    --fields=+nKSEi \
    -R \
    -f "$CTAGS_OUTPUT" \
    "${scan_paths[@]}"

log "Collecting raw service, permission, AIDL, and build facts"
rg --json \
    'ServiceManager\.addService|publishBinderService|LocalServices\.addService' \
    "$FW_BASE/services" \
    > "$PROJECT_ROOT/data/raw/service/registrations.jsonl" || true

rg --json \
    'enforceCallingPermission|checkCallingPermission|checkCallingOrSelfPermission|enforceCallingOrSelfPermission|enforcePermission|checkPermission|Manifest\.permission\.[A-Z0-9_]+' \
    "$FW_BASE/services" \
    > "$PROJECT_ROOT/data/raw/permission/checks.jsonl" || true

find "$FW_BASE" -type f -name '*.aidl' -print0 |
    sort -z |
    xargs -0 -r sha256sum \
    > "$PROJECT_ROOT/data/raw/aidl/files.sha256"

find "$FW_BASE" -type f -name 'Android.bp' -print0 |
    sort -z |
    xargs -0 -r sha256sum \
    > "$PROJECT_ROOT/data/raw/build/android-bp.sha256"

log "Resetting SQLite database"
rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
sqlite3 "$DB_PATH" < "$PROJECT_ROOT/storage/schema.sql"

log "Importing Java Symbol Graph"
python -m collectors.source.ctags_importer \
    "$CTAGS_OUTPUT" \
    "$DB_PATH" \
    "$AOSP_ROOT"

log "Importing AIDL/Binder Graph"
python -m collectors.binder.aidl_binder_importer \
    --frameworks-base "$FW_BASE" \
    --source-root "$AOSP_ROOT" \
    --db "$DB_PATH" \
    --raw-report \
    "$PROJECT_ROOT/data/raw/aidl/aidl-binder-report.json"

log "Importing Java Inheritance Graph"
python -m collectors.source.java_inheritance_importer \
    --ctags-jsonl "$CTAGS_OUTPUT" \
    --source-root "$AOSP_ROOT" \
    --db "$DB_PATH" \
    --report \
    "$PROJECT_ROOT/data/raw/inheritance/java-inheritance-report.json"

log "Importing System Service Registration Graph"
python -m collectors.service.service_registration_importer \
    --frameworks-base "$FW_BASE" \
    --source-root "$AOSP_ROOT" \
    --db "$DB_PATH" \
    --report \
    "$PROJECT_ROOT/data/raw/service/service-registration-report.json"

log "Validating foreign keys"
FK_ERRORS="$(sqlite3 "$DB_PATH" 'PRAGMA foreign_key_check;')"
if [[ -n "$FK_ERRORS" ]]; then
    printf '%s\n' "$FK_ERRORS"
    die "foreign_key_check failed"
fi
echo "foreign_key_check: PASS"

log "Validating ActivityManagerService symbol"
AMS_CLASS="$(
    sqlite3 "$DB_PATH" "
    SELECT COUNT(*)
    FROM node
    WHERE node_type='JAVA_CLASS'
      AND qualified_name=
          'com.android.server.am.ActivityManagerService';
    "
)"
[[ "$AMS_CLASS" == "1" ]] ||
    die "ActivityManagerService class validation failed"

log "Validating AMS Binder relation"
AMS_BINDER="$(
    sqlite3 "$DB_PATH" "
    SELECT COUNT(*)
    FROM edge e
    JOIN node impl ON impl.node_id=e.from_node_id
    JOIN node aidl ON aidl.node_id=e.to_node_id
    WHERE e.edge_type='IMPLEMENTS_BINDER'
      AND impl.qualified_name=
          'com.android.server.am.ActivityManagerService'
      AND aidl.qualified_name=
          'android.app.IActivityManager';
    "
)"
[[ "$AMS_BINDER" -ge 1 ]] ||
    die "AMS -> IActivityManager validation failed"

log "Validating PackageManager inheritance"
PMS_INHERITANCE="$(
    sqlite3 "$DB_PATH" "
    SELECT COUNT(*)
    FROM edge e
    JOIN node child
      ON child.node_id=e.from_node_id
    JOIN node parent
      ON parent.node_id=e.to_node_id
    WHERE e.edge_type='EXTENDS'
      AND child.qualified_name=
          'com.android.server.pm.PackageManagerService.IPackageManagerImpl'
      AND parent.qualified_name=
          'com.android.server.pm.IPackageManagerBase';
    "
)"
[[ "$PMS_INHERITANCE" -ge 1 ]] ||
    die "PackageManager inheritance validation failed"

log "Validating core service registrations"
ACTIVITY_SERVICE_COUNT="$(
    sqlite3 "$DB_PATH" "
    SELECT COUNT(*)
    FROM edge e
    JOIN node impl
      ON impl.node_id=e.from_node_id
    JOIN node service
      ON service.node_id=e.to_node_id
    WHERE e.edge_type='REGISTERED_AS'
      AND service.qualified_name='activity'
      AND impl.qualified_name=
          'com.android.server.am.ActivityManagerService';
    "
)"
[[ "$ACTIVITY_SERVICE_COUNT" -ge 1 ]] ||
    die "Activity service registration validation failed"

PACKAGE_SERVICE_COUNT="$(
    sqlite3 "$DB_PATH" "
    SELECT COUNT(*)
    FROM edge e
    JOIN node impl
      ON impl.node_id=e.from_node_id
    JOIN node service
      ON service.node_id=e.to_node_id
    WHERE e.edge_type='REGISTERED_AS'
      AND service.qualified_name='package'
      AND impl.qualified_name=
          'com.android.server.pm.PackageManagerService.IPackageManagerImpl';
    "
)"
[[ "$PACKAGE_SERVICE_COUNT" -ge 1 ]] ||
    die "Package service registration validation failed"

log "Graph summary"
sqlite3 -header -column "$DB_PATH" \
    < "$PROJECT_ROOT/queries/summary.sql"

log "AMS Binder relation"
sqlite3 -header -column "$DB_PATH" \
    < "$PROJECT_ROOT/queries/ams_binder.sql"

log "Package Manager direct Binder base"
sqlite3 -header -column "$DB_PATH" \
    < "$PROJECT_ROOT/queries/package_manager_binder.sql"

log "Rebuild completed"
