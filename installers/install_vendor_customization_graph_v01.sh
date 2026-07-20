#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/ts/android-context-intelligence}"

log() {
    printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"
}

die() {
    printf '\n[ERROR] %s\n' "$*" >&2
    exit 1
}

[[ -d "$PROJECT_ROOT" ]] || die "Project root not found: $PROJECT_ROOT"

log "Creating Vendor Extension Collector structure"
mkdir -p \
    "$PROJECT_ROOT/data/raw/vendor" \
    "$PROJECT_ROOT/data/staging/vendor_src" \
    "$PROJECT_ROOT/collectors/vendor"

touch "$PROJECT_ROOT/collectors/vendor/__init__.py"

log "Writing Vendor Graph Integration script v0.1"

cat > "$PROJECT_ROOT/scripts/import_vendor.sh" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"
VENDOR_RAW="$PROJECT_ROOT/data/raw/vendor"
VENDOR_SRC="$PROJECT_ROOT/data/staging/vendor_src"
DB_PATH="$PROJECT_ROOT/data/android_context.db"
CTAGS_OUT="$PROJECT_ROOT/data/staging/vendor_ctags.jsonl"

log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }

mkdir -p "$VENDOR_RAW" "$VENDOR_SRC"

# Check if there are any JAR or APK files to process
shopt -s nullglob
files=("$VENDOR_RAW"/*.jar "$VENDOR_RAW"/*.apk)
shopt -u nullglob

if [ ${#files[@]} -eq 0 ]; then
    log "No .jar or .apk found in $VENDOR_RAW. Please add vendor files and re-run."
    exit 0
fi

JADX_BIN=~/jadx-1.5.6/bin/jadx
if [ ! -f "$JADX_BIN" ]; then
    log "ERROR: 'jadx' command not found at $JADX_BIN. Please install jadx."
    exit 1
fi

for file in "${files[@]}"; do
    name=$(basename "$file")
    out_dir="$VENDOR_SRC/${name%.*}"
    if [ ! -d "$out_dir" ]; then
        log "Decompiling $name with jadx..."
        if ! "$JADX_BIN" -d "$out_dir" --no-res --no-debug-info --threads-count 4 "$file"; then
            log "ERROR: Failed to decompile $name"
            exit 1
        fi
    else
        log "Directory $out_dir already exists. Skipping decompilation for $name."
    fi
done

log "Extracting symbols from vendor sources via Universal Ctags..."
# Reuse identical ctags configuration from AOSP baseline
if ! ctags --options=NONE --fields=+nKSE --extras=+q -R --languages=Java,Kotlin --output-format=json "$VENDOR_SRC" > "$CTAGS_OUT"; then
    log "ERROR: Failed to run ctags"
    exit 1
fi

log "Importing vendor symbols into Android Context Graph..."
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

# Stage 1: Import basic symbols (classes, methods, fields)
log "Importing Java symbols..."
if ! python -m collectors.source.ctags_importer "$CTAGS_OUT" "$DB_PATH" "$VENDOR_SRC"; then
    log "ERROR: Failed to import Java symbols"
    exit 1
fi

log "Importing Kotlin symbols..."
if ! python -m collectors.source.ctags_importer --language kotlin "$CTAGS_OUT" "$DB_PATH" "$VENDOR_SRC"; then
    log "ERROR: Failed to import Kotlin symbols"
    exit 1
fi

# Stage 2: Extract and resolve cross-references (extends, implements)
# This magically bridges vendor classes to existing AOSP nodes!
log "Running inheritance resolution..."
if ! python -m collectors.source.java_inheritance_importer \
    --ctags-jsonl "$CTAGS_OUT" \
    --db "$DB_PATH" \
    --source-root "$VENDOR_SRC" \
    --report "$VENDOR_SRC/inheritance_report.json"; then
    log "ERROR: Failed to resolve inheritance"
    exit 1
fi

log "Vendor Graph Integration completed successfully."
SH

chmod +x "$PROJECT_ROOT/scripts/import_vendor.sh"

log "Vendor Customization Graph v0.1 completed."
echo ""
echo "================================================================"
echo "Usage Instructions:"
echo "1. Place vendor artifacts (.jar, .apk) in:"
echo "   $PROJECT_ROOT/data/raw/vendor/"
echo "2. Run the integration pipeline:"
echo "   cd $PROJECT_ROOT && ./scripts/import_vendor.sh"
echo "================================================================"
