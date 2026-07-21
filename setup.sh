#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_ROOT="$SCRIPT_DIR/project"
CANONICAL_INSTALLER="$SCRIPT_DIR/installers/install_project.sh"
AOSP_ROOT="${AOSP_ROOT:-$HOME/aosp}"
PROJECT_ROOT="${PROJECT_ROOT:-$HOME/android-context-intelligence}"
MODE=""
REBUILD=0

usage() {
    printf '%s\n' \
        "Usage:" \
        "  ./setup.sh --fresh [--rebuild]" \
        "  ./setup.sh --upgrade [--rebuild]" \
        "  ./setup.sh --verify-only" \
        "" \
        "Environment:" \
        "  AOSP_ROOT=/path/to/aosp" \
        "  PROJECT_ROOT=/path/to/android-context-intelligence" \
        "  ANDROID_CONTEXT_SOURCE_COMMIT=<git-commit>"
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required rebuild command is missing: $1"
}

select_mode() {
    [[ -z "$MODE" ]] || die "exactly one install mode is required"
    MODE="$1"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fresh|--upgrade|--verify-only)
            select_mode "$1"
            shift
            ;;
        --rebuild)
            REBUILD=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "unknown option: $1"
            ;;
    esac
done

if [[ -z "$MODE" ]]; then
    usage >&2
    die "an install mode is required"
fi
if [[ "$MODE" == "--verify-only" && "$REBUILD" -eq 1 ]]; then
    die "--rebuild cannot be combined with --verify-only"
fi

[[ -d "$PAYLOAD_ROOT" ]] || die "canonical project payload is missing: $PAYLOAD_ROOT"
[[ -f "$CANONICAL_INSTALLER" ]] || die "canonical installer is missing: $CANONICAL_INSTALLER"

if [[ "$REBUILD" -eq 1 ]]; then
    [[ -d "$AOSP_ROOT" ]] || die "AOSP root is missing: $AOSP_ROOT"
    [[ -d "$AOSP_ROOT/frameworks/base" ]] ||
        die "AOSP frameworks/base is missing: $AOSP_ROOT/frameworks/base"
    for command in python3 ctags sqlite3 rg find sha256sum flock; do
        require_command "$command"
    done
    ctags --version | grep -qi "Universal Ctags" ||
        die "rebuild requires Universal Ctags"
fi

install_arguments=(
    "$MODE"
    --source "$PAYLOAD_ROOT"
    --target "$PROJECT_ROOT"
)
if [[ -n "${ANDROID_CONTEXT_SOURCE_COMMIT:-}" ]]; then
    install_arguments+=(--source-commit "$ANDROID_CONTEXT_SOURCE_COMMIT")
fi

bash "$CANONICAL_INSTALLER" "${install_arguments[@]}"

if [[ "$REBUILD" -eq 1 ]]; then
    if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
        python3 -m venv "$PROJECT_ROOT/.venv"
    fi
    "$PROJECT_ROOT/.venv/bin/python" -m pip install \
        --disable-pip-version-check \
        --requirement "$PROJECT_ROOT/requirements-lock.txt"
    export PYTHONPATH="$PROJECT_ROOT"
    bash "$PROJECT_ROOT/scripts/rebuild_all.sh"
fi

printf 'Android Context Intelligence setup: PASS\n'
printf 'Project: %s\n' "$PROJECT_ROOT"
if [[ "$MODE" != "--verify-only" ]]; then
    printf 'Verify: PROJECT_ROOT=%q bash %q --verify-only\n' "$PROJECT_ROOT" "$SCRIPT_DIR/setup.sh"
fi
