#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  ./setup_android_context_intelligence_complete_v01.sh --fresh
  ./setup_android_context_intelligence_complete_v01.sh --rebuild

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
)

for script in "${scripts[@]}"; do
  [[ -f "$SCRIPT_DIR/$script" ]] || die "Missing sibling script: $SCRIPT_DIR/$script"
  bash -n "$SCRIPT_DIR/$script"
done

export AOSP_ROOT PROJECT_ROOT

log "Stage 1/4: base Java Symbol and AIDL/Binder graph"
bash "$SCRIPT_DIR/setup_android_context_intelligence_v1.sh" "$MODE"

log "Stage 2/4: Java Inheritance graph"
bash "$SCRIPT_DIR/install_java_inheritance_graph_v01.sh"

log "Stage 3/4: System Service Registration graph"
bash "$SCRIPT_DIR/install_system_service_registration_graph_v01.sh"

log "Stage 4/4: Multi-Repository Source Configuration"
bash "$SCRIPT_DIR/install_multi_repository_source_configuration_v01.sh"

log "Complete installation verified"
echo "Project: $PROJECT_ROOT"
echo "Canonical rebuild: cd $PROJECT_ROOT && ./scripts/rebuild_all.sh"
