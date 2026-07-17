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

export PATH="/home/ts/jadx-1.5.6/bin:$PATH"
if ! command -v jadx >/dev/null 2>&1; then
    log "ERROR: 'jadx' command not found. Please install jadx and ensure it is in PATH."
    exit 1
fi

for file in "${files[@]}"; do
    name=$(basename "$file")
    out_dir="$VENDOR_SRC/${name%.*}"
    if [ ! -d "$out_dir" ]; then
        log "Decompiling $name with jadx..."
        jadx -d "$out_dir" --no-res "$file" || true
    else
        log "Directory $out_dir already exists. Skipping decompilation for $name."
    fi
done

log "Extracting symbols from vendor sources via Universal Ctags..."
# Reuse identical ctags configuration from AOSP baseline
ctags --options=NONE --fields=+nKSE --extras=+q -R --languages=Java,Kotlin --output-format=json "$VENDOR_SRC" > "$CTAGS_OUT"

log "Importing vendor symbols into Android Context Graph..."
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

# Stage 1: Import basic symbols (classes, methods, fields)
python -m collectors.source.ctags_importer "$CTAGS_OUT" "$DB_PATH" "$VENDOR_SRC"

# Stage 2: Extract and resolve cross-references (extends, implements)
# This magically bridges vendor classes to existing AOSP nodes!
python -m collectors.source.java_inheritance_importer "$CTAGS_OUT" "$DB_PATH" "$VENDOR_SRC"

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
