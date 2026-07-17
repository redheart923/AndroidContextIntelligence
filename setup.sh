#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLERS_DIR="$SCRIPT_DIR/installers"
AOSP_ROOT="${AOSP_ROOT:-/home/ts/aosp}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/ts/android-context-intelligence}"
MODE="${1:---fresh}"

log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf '\n[ERROR] %s\n' "$*" >&2; exit 1; }
trap 'die "failed at line $LINENO"' ERR

case "$MODE" in
  --fresh|--rebuild) ;;
  -h|--help)
    cat <<'EOF'
Usage:
  ./setup.sh --fresh
  ./setup.sh --rebuild

Environment:
  AOSP_ROOT=/home/ts/aosp
  PROJECT_ROOT=/home/ts/android-context-intelligence
EOF
    exit 0
    ;;
  *) die "Unknown mode: $MODE" ;;
esac

scripts=(
  setup_android_context_intelligence_v1.sh
  install_java_inheritance_graph_v01.sh
  install_system_service_registration_graph_v01.sh
  install_multi_repository_source_configuration_v01.sh
  install_vendor_customization_graph_v01.sh
  install_permission_enforcement_graph_v01.sh
)

for script in "${scripts[@]}"; do
  [[ -f "$INSTALLERS_DIR/$script" ]] || die "Missing installer script: $INSTALLERS_DIR/$script"
  bash -n "$INSTALLERS_DIR/$script"
done

export AOSP_ROOT PROJECT_ROOT

log "Stage 1/6: base Java Symbol and AIDL/Binder graph"
bash "$INSTALLERS_DIR/setup_android_context_intelligence_v1.sh" "$MODE"

log "Stage 2/6: Java Inheritance graph"
bash "$INSTALLERS_DIR/install_java_inheritance_graph_v01.sh"

log "Stage 3/6: System Service Registration graph"
bash "$INSTALLERS_DIR/install_system_service_registration_graph_v01.sh"

log "Stage 4/6: Multi-Repository Source Configuration"
bash "$INSTALLERS_DIR/install_multi_repository_source_configuration_v01.sh"

log "Stage 5/6: Vendor Customization Graph Integration"
bash "$INSTALLERS_DIR/install_vendor_customization_graph_v01.sh"

log "Stage 6/6: Permission Enforcement Graph"
bash "$INSTALLERS_DIR/install_permission_enforcement_graph_v01.sh"

log "Complete installation verified"
echo "Project: $PROJECT_ROOT"
echo "Canonical rebuild: cd $PROJECT_ROOT && ./scripts/rebuild_all.sh"
