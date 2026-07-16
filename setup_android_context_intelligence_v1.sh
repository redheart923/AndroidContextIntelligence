#!/usr/bin/env bash
set -Eeuo pipefail

AOSP_ROOT="${AOSP_ROOT:-/home/ts/aosp}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/ts/android-context-intelligence}"
FW_BASE="$AOSP_ROOT/frameworks/base"
DB_PATH="$PROJECT_ROOT/data/android_context.db"
CTAGS_OUTPUT="$PROJECT_ROOT/data/raw/ctags/frameworks-base.jsonl"
MODE="${1:---rebuild}"

log() {
    printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"
}

die() {
    printf '\n[ERROR] %s\n' "$*" >&2
    exit 1
}

trap 'die "failed at line $LINENO"' ERR

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

usage() {
    cat <<'EOF'
Usage:
  setup_android_context_intelligence_v1.sh --fresh
  setup_android_context_intelligence_v1.sh --rebuild

--fresh:
  Back up the whole existing project directory, then build a clean project.

--rebuild:
  Keep the project directory, overwrite generated source/configuration files,
  reset the database, and rebuild all current graph layers.

Environment overrides:
  AOSP_ROOT=/path/to/aosp
  PROJECT_ROOT=/path/to/android-context-intelligence
EOF
}

case "$MODE" in
    --fresh|--rebuild) ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage
        die "Unknown mode: $MODE"
        ;;
esac

log "Checking environment"

[[ -d "$FW_BASE" ]] || die "Missing frameworks/base: $FW_BASE"
[[ -d "$FW_BASE/core" ]] || die "Missing frameworks/base/core"
[[ -d "$FW_BASE/services" ]] || die "Missing frameworks/base/services"

for command in python3 git ctags sqlite3 rg find sha256sum; do
    require_command "$command"
done

ctags --version | grep -qi "Universal Ctags" ||
    die "ctags must be Universal Ctags"

if [[ "$MODE" == "--fresh" && -e "$PROJECT_ROOT" ]]; then
    BACKUP="${PROJECT_ROOT}.backup.$(date '+%Y%m%d-%H%M%S')"
    log "Backing up existing project to $BACKUP"
    mv "$PROJECT_ROOT" "$BACKUP"
fi

log "Creating clean project structure"

mkdir -p \
    "$PROJECT_ROOT/configs" \
    "$PROJECT_ROOT/docs" \
    "$PROJECT_ROOT/collectors/source" \
    "$PROJECT_ROOT/collectors/binder" \
    "$PROJECT_ROOT/graph" \
    "$PROJECT_ROOT/storage" \
    "$PROJECT_ROOT/scripts" \
    "$PROJECT_ROOT/queries" \
    "$PROJECT_ROOT/tests/unit" \
    "$PROJECT_ROOT/data/raw/ctags" \
    "$PROJECT_ROOT/data/raw/aidl" \
    "$PROJECT_ROOT/data/raw/service" \
    "$PROJECT_ROOT/data/raw/permission" \
    "$PROJECT_ROOT/data/raw/build"

touch \
    "$PROJECT_ROOT/collectors/__init__.py" \
    "$PROJECT_ROOT/collectors/source/__init__.py" \
    "$PROJECT_ROOT/collectors/binder/__init__.py" \
    "$PROJECT_ROOT/graph/__init__.py" \
    "$PROJECT_ROOT/tests/__init__.py" \
    "$PROJECT_ROOT/tests/unit/__init__.py"

[[ -d "$PROJECT_ROOT/.git" ]] || git -C "$PROJECT_ROOT" init >/dev/null

cat > "$PROJECT_ROOT/.gitignore" <<'EOF'
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/raw/
data/*.db
data/*.db-*
EOF

cat > "$PROJECT_ROOT/configs/local.yaml" <<EOF
source_root: "$AOSP_ROOT"
frameworks_base: "$FW_BASE"
database:
  type: sqlite
  path: "$DB_PATH"
EOF

log "Creating Python environment"

if [[ ! -d "$PROJECT_ROOT/.venv" ]]; then
    python3 -m venv "$PROJECT_ROOT/.venv"
fi

source "$PROJECT_ROOT/.venv/bin/activate"
python -m pip install --quiet --upgrade pip setuptools wheel
python -m pip install --quiet pytest
python -m pip freeze > "$PROJECT_ROOT/requirements-lock.txt"
export PYTHONPATH="$PROJECT_ROOT"

log "Writing schema and graph writer"

cat > "$PROJECT_ROOT/storage/schema.sql" <<'SQL'
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS node (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  qualified_name TEXT,
  display_name TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_node_type ON node(node_type);
CREATE INDEX IF NOT EXISTS idx_node_name ON node(qualified_name);
CREATE INDEX IF NOT EXISTS idx_node_source ON node(source_path);

CREATE TABLE IF NOT EXISTS edge (
  edge_id TEXT PRIMARY KEY,
  edge_type TEXT NOT NULL,
  from_node_id TEXT NOT NULL,
  to_node_id TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL,
  FOREIGN KEY(from_node_id) REFERENCES node(node_id),
  FOREIGN KEY(to_node_id) REFERENCES node(node_id)
);

CREATE INDEX IF NOT EXISTS idx_edge_from ON edge(from_node_id);
CREATE INDEX IF NOT EXISTS idx_edge_to ON edge(to_node_id);
CREATE INDEX IF NOT EXISTS idx_edge_type ON edge(edge_type);

SQL

cat > "$PROJECT_ROOT/graph/writer.py" <<'PY'
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "1.0.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(kind: str, identity: str) -> str:
    return f"{kind}:{identity}"


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: str
    display_name: str
    qualified_name: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    extractor: str = "unknown"


@dataclass(frozen=True)
class Edge:
    edge_type: str
    from_node_id: str
    to_node_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    extractor: str = "unknown"

    @property
    def edge_id(self) -> str:
        identity = "|".join(
            [
                self.edge_type,
                self.from_node_id,
                self.to_node_id,
                self.source_path or "",
                str(self.line_start or ""),
            ]
        )
        return stable_hash(identity)


class GraphWriter:
    def __init__(
        self,
        db_path: Path,
        source_revision: str = "unknown",
    ) -> None:
        self.source_revision = source_revision
        self.c = sqlite3.connect(db_path)
        self.c.execute("PRAGMA foreign_keys=ON")

    def upsert_node(self, node: Node) -> None:
        properties_json = json.dumps(
            node.properties,
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = stable_hash(
            "|".join(
                [
                    node.node_type,
                    node.qualified_name or "",
                    node.display_name,
                    properties_json,
                    node.source_path or "",
                    str(node.line_start or ""),
                    str(node.line_end or ""),
                ]
            )
        )

        self.c.execute(
            """
            INSERT INTO node VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            ON CONFLICT(node_id) DO UPDATE SET
                node_type=excluded.node_type,
                qualified_name=excluded.qualified_name,
                display_name=excluded.display_name,
                properties_json=excluded.properties_json,
                source_path=excluded.source_path,
                line_start=excluded.line_start,
                line_end=excluded.line_end,
                source_revision=excluded.source_revision,
                extractor=excluded.extractor,
                extractor_version=excluded.extractor_version,
                content_hash=excluded.content_hash,
                status='active',
                updated_at=excluded.updated_at
            """,
            (
                node.node_id,
                node.node_type,
                node.qualified_name,
                node.display_name,
                properties_json,
                node.source_path,
                node.line_start,
                node.line_end,
                self.source_revision,
                node.extractor,
                EXTRACTOR_VERSION,
                content_hash,
                "active",
                now_iso(),
            ),
        )

    def upsert_edge(self, edge: Edge) -> None:
        properties_json = json.dumps(
            edge.properties,
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = stable_hash(
            "|".join(
                [
                    edge.edge_type,
                    edge.from_node_id,
                    edge.to_node_id,
                    properties_json,
                ]
            )
        )

        self.c.execute(
            """
            INSERT INTO edge (
                edge_id,
                edge_type,
                from_node_id,
                to_node_id,
                properties_json,
                source_path,
                line_start,
                line_end,
                source_revision,
                extractor,
                extractor_version,
                content_hash,
                status,
                updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(edge_id) DO UPDATE SET
                properties_json=excluded.properties_json,
                source_path=excluded.source_path,
                line_start=excluded.line_start,
                line_end=excluded.line_end,
                source_revision=excluded.source_revision,
                extractor=excluded.extractor,
                extractor_version=excluded.extractor_version,
                content_hash=excluded.content_hash,
                status='active',
                updated_at=excluded.updated_at
            """,
            (
                edge.edge_id,
                edge.edge_type,
                edge.from_node_id,
                edge.to_node_id,
                properties_json,
                edge.source_path,
                edge.line_start,
                edge.line_end,
                self.source_revision,
                edge.extractor,
                EXTRACTOR_VERSION,
                content_hash,
                "active",
                now_iso(),
            ),
        )

    def close(self) -> None:
        self.c.commit()
        self.c.close()

PY

log "Writing Java Symbol Graph importer v0.2.2"

cat > "$PROJECT_ROOT/collectors/source/ctags_importer.py" <<'PY'
from __future__ import annotations

import json
import re
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path

from graph.writer import Edge, GraphWriter, Node, stable_id


KIND_MAP = {
    "class": "JAVA_CLASS",
    "interface": "JAVA_INTERFACE",
    "method": "JAVA_METHOD",
    "field": "JAVA_FIELD",
    "package": "JAVA_PACKAGE",
    "enum": "JAVA_ENUM",
    "enumConstant": "JAVA_ENUM_CONSTANT",
    "annotation": "JAVA_ANNOTATION",
}

OWNER_KIND_MAP = {
    "class": "JAVA_CLASS",
    "interface": "JAVA_INTERFACE",
    "enum": "JAVA_ENUM",
    "annotation": "JAVA_ANNOTATION",
}

PACKAGE_RE = re.compile(
    r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;",
    re.MULTILINE,
)


@lru_cache(maxsize=65536)
def read_package_name(absolute_path: str) -> str:
    path = Path(absolute_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    match = PACKAGE_RE.search(text)
    return match.group(1) if match else ""


def normalize_path(path: str, source_root: Path) -> str:
    value = Path(path)
    try:
        return str(value.resolve().relative_to(source_root.resolve()))
    except ValueError:
        return str(value)


def qualify(package_name: str, local_name: str) -> str:
    if package_name and local_name:
        return f"{package_name}.{local_name}"
    return local_name or package_name


def normalize_scope(scope: str | None, package_name: str) -> str:
    if not scope:
        return ""

    value = scope.replace(":", ".")
    if package_name and value.startswith(package_name + "."):
        return value
    return qualify(package_name, value)


def build_identity(
    record: dict,
    package_name: str,
) -> tuple[str, str | None]:
    kind = record.get("kind")
    name = record.get("name", "")
    signature = record.get("signature") or ""
    scope = normalize_scope(record.get("scope"), package_name)

    if kind == "package":
        # Ctags may emit only the final segment as the name.
        return package_name or name, None

    if kind in {"class", "interface", "enum", "annotation"}:
        if scope:
            qualified_name = f"{scope}.{name}"
        else:
            qualified_name = qualify(package_name, name)
        return qualified_name, scope or package_name or None

    if kind == "method":
        owner = scope or package_name
        qualified_name = (
            f"{owner}#{name}{signature}"
            if owner
            else f"{name}{signature}"
        )
        return qualified_name, owner or None

    if kind in {"field", "enumConstant"}:
        owner = scope or package_name
        qualified_name = f"{owner}#{name}" if owner else name
        return qualified_name, owner or None

    return qualify(package_name, name), scope or package_name or None


def iter_records(input_path: Path):
    with input_path.open("r", encoding="utf-8") as stream:
        for raw_line in stream:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            if record.get("_type") == "tag":
                yield record


def collect_owner_ids(db_path: Path) -> set[str]:
    node_types = tuple(OWNER_KIND_MAP.values())
    placeholders = ",".join("?" for _ in node_types)

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT node_id
            FROM node
            WHERE node_type IN ({placeholders})
            """,
            node_types,
        )
        return {row[0] for row in rows}


def first_pass(
    input_path: Path,
    db_path: Path,
    source_root: Path,
) -> tuple[int, int]:
    writer = GraphWriter(db_path)
    imported = 0
    skipped = 0

    try:
        for record in iter_records(input_path):
            kind = record.get("kind")
            node_type = KIND_MAP.get(kind)
            if not node_type:
                continue

            raw_path = record.get("path", "")
            source_path = normalize_path(raw_path, source_root)
            package_name = read_package_name(
                str(Path(raw_path).resolve())
            )
            qualified_name, owner_qname = build_identity(
                record,
                package_name,
            )

            if not qualified_name:
                skipped += 1
                continue

            line = record.get("line")
            node = Node(
                node_id=stable_id(node_type, qualified_name),
                node_type=node_type,
                display_name=record.get("name", qualified_name),
                qualified_name=qualified_name,
                properties={
                    "kind": kind,
                    "package": package_name or None,
                    "scope": record.get("scope"),
                    "scopeKind": record.get("scopeKind"),
                    "owner": owner_qname,
                    "signature": record.get("signature"),
                    "access": record.get("access"),
                    "pattern": record.get("pattern"),
                },
                source_path=source_path,
                line_start=line,
                line_end=line,
                extractor="universal-ctags-v0.2.1",
            )
            writer.upsert_node(node)

            file_id = stable_id("FILE", source_path)
            writer.upsert_node(
                Node(
                    node_id=file_id,
                    node_type="FILE",
                    display_name=Path(source_path).name,
                    qualified_name=source_path,
                    source_path=source_path,
                    extractor="file-indexer-v0.2.1",
                )
            )
            writer.upsert_edge(
                Edge(
                    edge_type="DECLARED_IN",
                    from_node_id=node.node_id,
                    to_node_id=file_id,
                    source_path=source_path,
                    line_start=line,
                    line_end=line,
                    extractor="universal-ctags-v0.2.1",
                )
            )

            imported += 1

    finally:
        writer.close()

    return imported, skipped


def second_pass(
    input_path: Path,
    db_path: Path,
    source_root: Path,
) -> tuple[int, int]:
    owner_ids = collect_owner_ids(db_path)
    writer = GraphWriter(db_path)
    inserted = 0
    missing_owner = 0

    try:
        for record in iter_records(input_path):
            kind = record.get("kind")
            if kind not in {"method", "field", "enumConstant"}:
                continue

            owner_type = OWNER_KIND_MAP.get(
                record.get("scopeKind") or ""
            )
            if not owner_type:
                missing_owner += 1
                continue

            raw_path = record.get("path", "")
            source_path = normalize_path(raw_path, source_root)
            package_name = read_package_name(
                str(Path(raw_path).resolve())
            )
            qualified_name, owner_qname = build_identity(
                record,
                package_name,
            )

            if not qualified_name or not owner_qname:
                missing_owner += 1
                continue

            owner_id = stable_id(owner_type, owner_qname)
            if owner_id not in owner_ids:
                # Local/anonymous classes and some ctags scopes may not have
                # a corresponding top-level type record. Do not create a
                # broken foreign-key edge.
                missing_owner += 1
                continue

            node_type = KIND_MAP[kind]
            member_id = stable_id(node_type, qualified_name)
            relation = (
                "HAS_METHOD"
                if kind == "method"
                else "HAS_MEMBER"
            )
            line = record.get("line")

            writer.upsert_edge(
                Edge(
                    edge_type=relation,
                    from_node_id=owner_id,
                    to_node_id=member_id,
                    source_path=source_path,
                    line_start=line,
                    line_end=line,
                    extractor="universal-ctags-v0.2.1",
                )
            )
            inserted += 1

    finally:
        writer.close()

    return inserted, missing_owner


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: python -m collectors.source.ctags_importer "
            "<ctags-jsonl> <db-path> <source-root>"
        )
        return 2

    input_path = Path(sys.argv[1])
    db_path = Path(sys.argv[2])
    source_root = Path(sys.argv[3])

    imported, skipped = first_pass(
        input_path,
        db_path,
        source_root,
    )
    owner_edges, missing_owner = second_pass(
        input_path,
        db_path,
        source_root,
    )

    print(
        f"Imported {imported} Java symbols; "
        f"skipped {skipped}; "
        f"owner edges {owner_edges}; "
        f"unresolved owners {missing_owner}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

log "Writing AIDL/Binder Graph importer v0.1"

cat > "$PROJECT_ROOT/collectors/binder/aidl_binder_importer.py" <<'PY'
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from graph.writer import Edge, GraphWriter, Node, stable_id


PACKAGE_RE = re.compile(
    r"\bpackage\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;"
)

IMPORT_RE = re.compile(
    r"\bimport\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;"
)

AIDL_INTERFACE_RE = re.compile(
    r"\b(?:oneway\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{",
    re.MULTILINE,
)

JAVA_CLASS_RE = re.compile(
    r"""
    \bclass\s+
    (?P<class_name>[A-Za-z_][A-Za-z0-9_]*)
    (?P<between>.*?)
    \bextends\s+
    (?P<aidl_name>[A-Za-z_][A-Za-z0-9_$.]*)\.Stub
    """,
    re.DOTALL | re.VERBOSE,
)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"//[^\n]*")
ANNOTATION_RE = re.compile(
    r"@[A-Za-z_][A-Za-z0-9_.]*(?:\([^)]*\))?"
)


@dataclass(frozen=True)
class AidlMethod:
    name: str
    return_type: str
    signature: str
    declaration: str


@dataclass(frozen=True)
class AidlInterface:
    package_name: str
    interface_name: str
    qualified_name: str
    imports: tuple[str, ...]
    methods: tuple[AidlMethod, ...]
    source_path: Path


@dataclass(frozen=True)
class BinderImplementation:
    implementation_qname: str
    aidl_qname: str
    source_path: Path
    class_name: str
    aidl_simple_name: str


def remove_comments(text: str) -> str:
    text = BLOCK_COMMENT_RE.sub("", text)
    return LINE_COMMENT_RE.sub("", text)


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def find_matching_brace(text: str, opening_index: int) -> int:
    depth = 0
    for index in range(opening_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unclosed interface body")


def split_top_level_semicolon_statements(body: str) -> list[str]:
    statements: list[str] = []
    start = 0
    paren_depth = 0
    angle_depth = 0
    square_depth = 0

    for index, char in enumerate(body):
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif (
            char == ";"
            and paren_depth == 0
            and angle_depth == 0
            and square_depth == 0
        ):
            statement = body[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1

    return statements


def parse_aidl_method(statement: str) -> AidlMethod | None:
    cleaned = ANNOTATION_RE.sub("", statement)
    cleaned = normalize_whitespace(cleaned)

    if not cleaned or "(" not in cleaned or ")" not in cleaned:
        return None

    if cleaned.startswith(
        (
            "const ",
            "parcelable ",
            "enum ",
            "union ",
            "interface ",
        )
    ):
        return None

    match = re.match(
        r"""
        ^(?:oneway\s+)?
        (?P<return_type>[A-Za-z_][A-Za-z0-9_.$<>\[\]?]*)
        \s+
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)
        \s*
        \((?P<params>.*)\)
        (?:\s+throws\s+.*)?$
        """,
        cleaned,
        re.VERBOSE,
    )

    if not match:
        return None

    params = normalize_whitespace(match.group("params"))
    signature = f"({params})"

    return AidlMethod(
        name=match.group("name"),
        return_type=match.group("return_type"),
        signature=signature,
        declaration=cleaned,
    )


def parse_aidl(path: Path) -> AidlInterface:
    text = remove_comments(
        path.read_text(encoding="utf-8", errors="replace")
    )

    package_match = PACKAGE_RE.search(text)
    if not package_match:
        raise ValueError(f"Missing AIDL package: {path}")

    interface_match = AIDL_INTERFACE_RE.search(text)
    if not interface_match:
        raise ValueError(f"Missing AIDL interface: {path}")

    package_name = package_match.group(1)
    interface_name = interface_match.group(1)
    opening_index = text.find("{", interface_match.start())
    closing_index = find_matching_brace(text, opening_index)
    body = text[opening_index + 1 : closing_index]

    methods = tuple(
        method
        for statement in split_top_level_semicolon_statements(body)
        if (method := parse_aidl_method(statement)) is not None
    )

    imports = tuple(IMPORT_RE.findall(text))

    return AidlInterface(
        package_name=package_name,
        interface_name=interface_name,
        qualified_name=f"{package_name}.{interface_name}",
        imports=imports,
        methods=methods,
        source_path=path,
    )


def resolve_aidl_qname(
    aidl_reference: str,
    imports: dict[str, str],
    package_name: str,
    known_aidl_by_simple_name: dict[str, set[str]],
) -> str | None:
    normalized = aidl_reference.replace("$", ".")
    simple_name = normalized.rsplit(".", 1)[-1]

    if "." in normalized:
        first_segment = normalized.split(".", 1)[0]
        if first_segment and first_segment[0].islower():
            return normalized

    if simple_name in imports:
        return imports[simple_name]

    same_package = (
        f"{package_name}.{simple_name}"
        if package_name
        else simple_name
    )
    candidates = known_aidl_by_simple_name.get(simple_name, set())

    if same_package in candidates:
        return same_package

    if len(candidates) == 1:
        return next(iter(candidates))

    return None


def parse_java_binder_implementations(
    path: Path,
    known_aidl_by_simple_name: dict[str, set[str]],
) -> list[BinderImplementation]:
    text = remove_comments(
        path.read_text(encoding="utf-8", errors="replace")
    )

    package_match = PACKAGE_RE.search(text)
    package_name = package_match.group(1) if package_match else ""

    imports = {
        qualified.rsplit(".", 1)[-1]: qualified
        for qualified in IMPORT_RE.findall(text)
    }

    results: list[BinderImplementation] = []

    for match in JAVA_CLASS_RE.finditer(text):
        class_name = match.group("class_name")
        aidl_reference = match.group("aidl_name")
        aidl_simple_name = aidl_reference.rsplit(".", 1)[-1]

        aidl_qname = resolve_aidl_qname(
            aidl_reference,
            imports,
            package_name,
            known_aidl_by_simple_name,
        )
        if not aidl_qname:
            continue

        implementation_qname = (
            f"{package_name}.{class_name}"
            if package_name
            else class_name
        )

        results.append(
            BinderImplementation(
                implementation_qname=implementation_qname,
                aidl_qname=aidl_qname,
                source_path=path,
                class_name=class_name,
                aidl_simple_name=aidl_simple_name,
            )
        )

    return results


def normalize_source_path(path: Path, source_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(source_root.resolve()))
    except ValueError:
        return str(path.resolve())


def node_exists(
    connection: sqlite3.Connection,
    node_id: str,
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM node WHERE node_id = ? LIMIT 1",
        (node_id,),
    ).fetchone()
    return row is not None


def import_aidl_interfaces(
    writer: GraphWriter,
    interfaces: Iterable[AidlInterface],
    source_root: Path,
) -> tuple[int, int]:
    interface_count = 0
    method_count = 0

    for interface in interfaces:
        source_path = normalize_source_path(
            interface.source_path,
            source_root,
        )
        file_id = stable_id("FILE", source_path)
        interface_id = stable_id(
            "AIDL_INTERFACE",
            interface.qualified_name,
        )

        writer.upsert_node(
            Node(
                node_id=file_id,
                node_type="FILE",
                display_name=Path(source_path).name,
                qualified_name=source_path,
                source_path=source_path,
                extractor="aidl-parser-v0.1",
            )
        )

        writer.upsert_node(
            Node(
                node_id=interface_id,
                node_type="AIDL_INTERFACE",
                display_name=interface.interface_name,
                qualified_name=interface.qualified_name,
                properties={
                    "package": interface.package_name,
                    "imports": list(interface.imports),
                },
                source_path=source_path,
                extractor="aidl-parser-v0.1",
            )
        )

        writer.upsert_edge(
            Edge(
                edge_type="DECLARED_IN",
                from_node_id=interface_id,
                to_node_id=file_id,
                source_path=source_path,
                extractor="aidl-parser-v0.1",
            )
        )

        for method in interface.methods:
            method_qname = (
                f"{interface.qualified_name}"
                f"#{method.name}{method.signature}"
            )
            method_id = stable_id("AIDL_METHOD", method_qname)

            writer.upsert_node(
                Node(
                    node_id=method_id,
                    node_type="AIDL_METHOD",
                    display_name=method.name,
                    qualified_name=method_qname,
                    properties={
                        "return_type": method.return_type,
                        "signature": method.signature,
                        "declaration": method.declaration,
                    },
                    source_path=source_path,
                    extractor="aidl-parser-v0.1",
                )
            )

            writer.upsert_edge(
                Edge(
                    edge_type="AIDL_HAS_METHOD",
                    from_node_id=interface_id,
                    to_node_id=method_id,
                    source_path=source_path,
                    extractor="aidl-parser-v0.1",
                )
            )
            writer.upsert_edge(
                Edge(
                    edge_type="DECLARED_IN",
                    from_node_id=method_id,
                    to_node_id=file_id,
                    source_path=source_path,
                    extractor="aidl-parser-v0.1",
                )
            )
            method_count += 1

        interface_count += 1

    return interface_count, method_count


def import_binder_relations(
    writer: GraphWriter,
    db_path: Path,
    relations: Iterable[BinderImplementation],
    source_root: Path,
) -> tuple[int, int]:
    inserted = 0
    unresolved = 0

    with sqlite3.connect(db_path) as connection:
        for relation in relations:
            implementation_id = stable_id(
                "JAVA_CLASS",
                relation.implementation_qname,
            )
            aidl_id = stable_id(
                "AIDL_INTERFACE",
                relation.aidl_qname,
            )

            if not node_exists(connection, implementation_id):
                unresolved += 1
                continue

            if not node_exists(connection, aidl_id):
                unresolved += 1
                continue

            source_path = normalize_source_path(
                relation.source_path,
                source_root,
            )

            writer.upsert_edge(
                Edge(
                    edge_type="IMPLEMENTS_BINDER",
                    from_node_id=implementation_id,
                    to_node_id=aidl_id,
                    properties={
                        "pattern": "extends_aidl_stub",
                    },
                    source_path=source_path,
                    extractor="binder-implementation-v0.1",
                )
            )
            inserted += 1

    return inserted, unresolved


def scan_aidl_files(
    frameworks_base: Path,
) -> tuple[list[AidlInterface], list[tuple[Path, str]]]:
    interfaces: list[AidlInterface] = []
    failures: list[tuple[Path, str]] = []

    for path in frameworks_base.rglob("*.aidl"):
        try:
            parsed = parse_aidl(path)
        except ValueError as error:
            # AIDL parcelables, enums and unions are outside v0.1 scope.
            if "Missing AIDL interface" not in str(error):
                failures.append((path, str(error)))
            continue
        except OSError as error:
            failures.append((path, str(error)))
            continue

        interfaces.append(parsed)

    return interfaces, failures


def build_simple_name_index(
    interfaces: Iterable[AidlInterface],
) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}

    for interface in interfaces:
        index.setdefault(interface.interface_name, set()).add(
            interface.qualified_name
        )

    return index


def scan_java_binder_relations(
    frameworks_base: Path,
    known_aidl_by_simple_name: dict[str, set[str]],
) -> list[BinderImplementation]:
    relations: list[BinderImplementation] = []

    for path in frameworks_base.rglob("*.java"):
        try:
            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            continue

        if ".Stub" not in text or "extends" not in text:
            continue

        relations.extend(
            parse_java_binder_implementations(
                path,
                known_aidl_by_simple_name,
            )
        )

    return relations


def write_raw_report(
    output_path: Path,
    interfaces: Iterable[AidlInterface],
    relations: Iterable[BinderImplementation],
    failures: Iterable[tuple[Path, str]],
    source_root: Path,
) -> None:
    report = {
        "interfaces": [
            {
                "qualified_name": item.qualified_name,
                "source_path": normalize_source_path(
                    item.source_path,
                    source_root,
                ),
                "method_count": len(item.methods),
            }
            for item in interfaces
        ],
        "binder_implementations": [
            {
                "implementation": item.implementation_qname,
                "aidl_interface": item.aidl_qname,
                "source_path": normalize_source_path(
                    item.source_path,
                    source_root,
                ),
            }
            for item in relations
        ],
        "failures": [
            {
                "source_path": normalize_source_path(path, source_root),
                "error": error,
            }
            for path, error in failures
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frameworks-base", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--raw-report", type=Path, required=True)
    args = parser.parse_args()

    interfaces, failures = scan_aidl_files(args.frameworks_base)
    aidl_index = build_simple_name_index(interfaces)
    relations = scan_java_binder_relations(
        args.frameworks_base,
        aidl_index,
    )

    writer = GraphWriter(args.db)
    try:
        interface_count, method_count = import_aidl_interfaces(
            writer,
            interfaces,
            args.source_root,
        )
    finally:
        writer.close()

    # Open a new writer only after AIDL nodes have been committed.
    writer = GraphWriter(args.db)
    try:
        relation_count, unresolved_count = import_binder_relations(
            writer,
            args.db,
            relations,
            args.source_root,
        )
    finally:
        writer.close()

    write_raw_report(
        args.raw_report,
        interfaces,
        relations,
        failures,
        args.source_root,
    )

    print(
        f"AIDL interfaces: {interface_count}; "
        f"AIDL methods: {method_count}; "
        f"Binder relations: {relation_count}; "
        f"Unresolved Binder relations: {unresolved_count}; "
        f"AIDL parse failures: {len(failures)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

cat > "$PROJECT_ROOT/tests/unit/test_aidl_binder_importer.py" <<'PY'
from pathlib import Path

from collectors.binder.aidl_binder_importer import (
    parse_aidl,
    parse_java_binder_implementations,
)


def test_parse_aidl_interface_and_methods(tmp_path: Path) -> None:
    aidl = tmp_path / "ITestService.aidl"
    aidl.write_text(
        """
        package android.example;

        import android.os.Bundle;

        interface ITestService {
            int ping(int value);
            void send(in Bundle data);
        }
        """,
        encoding="utf-8",
    )

    parsed = parse_aidl(aidl)

    assert parsed.package_name == "android.example"
    assert parsed.interface_name == "ITestService"
    assert parsed.qualified_name == "android.example.ITestService"
    assert [method.name for method in parsed.methods] == [
        "ping",
        "send",
    ]


def test_parse_multiline_aidl_method(tmp_path: Path) -> None:
    aidl = tmp_path / "IMultiline.aidl"
    aidl.write_text(
        """
        package android.example;

        interface IMultiline {
            void execute(
                int userId,
                String packageName
            );
        }
        """,
        encoding="utf-8",
    )

    parsed = parse_aidl(aidl)

    assert len(parsed.methods) == 1
    assert parsed.methods[0].name == "execute"
    assert "int userId" in parsed.methods[0].signature
    assert "String packageName" in parsed.methods[0].signature


def test_parse_java_extends_aidl_stub(tmp_path: Path) -> None:
    java = tmp_path / "ExampleService.java"
    java.write_text(
        """
        package com.android.server.example;

        import android.example.ITestService;

        public class ExampleService extends ITestService.Stub {
        }
        """,
        encoding="utf-8",
    )

    relations = parse_java_binder_implementations(
        java,
        known_aidl_by_simple_name={
            "ITestService": {"android.example.ITestService"}
        },
    )

    assert len(relations) == 1
    assert (
        relations[0].implementation_qname
        == "com.android.server.example.ExampleService"
    )
    assert (
        relations[0].aidl_qname
        == "android.example.ITestService"
    )


def test_parse_java_multiline_class_declaration(tmp_path: Path) -> None:
    java = tmp_path / "ExampleService.java"
    java.write_text(
        """
        package com.android.server.example;

        import android.example.ITestService;

        public final class ExampleService
                extends ITestService.Stub
                implements Runnable {
            public void run() {}
        }
        """,
        encoding="utf-8",
    )

    relations = parse_java_binder_implementations(
        java,
        known_aidl_by_simple_name={
            "ITestService": {"android.example.ITestService"}
        },
    )

    assert len(relations) == 1
    assert relations[0].aidl_qname == "android.example.ITestService"
PY

cat > "$PROJECT_ROOT/queries/summary.sql" <<'SQL'
SELECT node_type, COUNT(*) AS count
FROM node
GROUP BY node_type
ORDER BY count DESC;

SELECT edge_type, COUNT(*) AS count
FROM edge
GROUP BY edge_type
ORDER BY count DESC;
SQL

cat > "$PROJECT_ROOT/queries/ams_binder.sql" <<'SQL'
SELECT
    impl.qualified_name AS implementation,
    aidl.qualified_name AS binder_interface,
    aidl.source_path AS aidl_file
FROM edge e
JOIN node impl ON impl.node_id = e.from_node_id
JOIN node aidl ON aidl.node_id = e.to_node_id
WHERE e.edge_type = 'IMPLEMENTS_BINDER'
  AND impl.qualified_name =
      'com.android.server.am.ActivityManagerService';
SQL

cat > "$PROJECT_ROOT/queries/package_manager_binder.sql" <<'SQL'
SELECT
    impl.qualified_name AS implementation,
    aidl.qualified_name AS binder_interface,
    impl.source_path
FROM edge e
JOIN node impl ON impl.node_id = e.from_node_id
JOIN node aidl ON aidl.node_id = e.to_node_id
WHERE e.edge_type = 'IMPLEMENTS_BINDER'
  AND aidl.qualified_name =
      'android.content.pm.IPackageManager'
ORDER BY impl.qualified_name;
SQL

log "Writing canonical rebuild script"

cat > "$PROJECT_ROOT/scripts/rebuild_all.sh" <<'SH'
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
    --fields=+nKSE \
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
SH

chmod +x "$PROJECT_ROOT/scripts/rebuild_all.sh"

cat > "$PROJECT_ROOT/README.md" <<EOF
# Android Context Intelligence

Current deterministic graph layers:

1. Java Symbol Graph v0.2.2
2. AIDL/Binder Graph v0.1

Canonical rebuild:

\`\`\`bash
cd "$PROJECT_ROOT"
./scripts/rebuild_all.sh
\`\`\`

Current database:

\`\`\`text
$DB_PATH
\`\`\`

Next layer:

- Java Inheritance Graph v0.1
- Binder transitive implementation query v0.1.1
EOF

log "Syntax checking generated Python and shell files"

python -m py_compile \
    "$PROJECT_ROOT/graph/writer.py" \
    "$PROJECT_ROOT/collectors/source/ctags_importer.py" \
    "$PROJECT_ROOT/collectors/binder/aidl_binder_importer.py"

bash -n "$PROJECT_ROOT/scripts/rebuild_all.sh"

log "Running canonical clean rebuild"
"$PROJECT_ROOT/scripts/rebuild_all.sh"

log "Writing installation manifest"

cat > "$PROJECT_ROOT/INSTALLATION_MANIFEST.txt" <<EOF
Installed: $(date -Iseconds)
Mode: $MODE
AOSP_ROOT: $AOSP_ROOT
PROJECT_ROOT: $PROJECT_ROOT
Database: $DB_PATH

Canonical entry point:
  $PROJECT_ROOT/scripts/rebuild_all.sh

Generated layers:
  Java Symbol Graph v0.2.2
  AIDL/Binder Graph v0.1

Next:
  Java Inheritance Graph v0.1
EOF

log "Consolidated baseline completed"

cat <<EOF

Use this single command from now on:

  cd "$PROJECT_ROOT"
  ./scripts/rebuild_all.sh

For a completely clean recreation:

  AOSP_ROOT="$AOSP_ROOT" \
  PROJECT_ROOT="$PROJECT_ROOT" \
  $0 --fresh

Old patch scripts are no longer part of the canonical workflow.
EOF

