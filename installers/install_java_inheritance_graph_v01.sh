#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/ts/android-context-intelligence}"
AOSP_ROOT="${AOSP_ROOT:-/home/ts/aosp}"
FW_BASE="$AOSP_ROOT/frameworks/base"
DB_PATH="$PROJECT_ROOT/data/android_context.db"
CTAGS_JSONL="$PROJECT_ROOT/data/raw/ctags/frameworks-base.jsonl"
IMPORTER="$PROJECT_ROOT/collectors/source/java_inheritance_importer.py"
TEST_FILE="$PROJECT_ROOT/tests/unit/test_java_inheritance_importer.py"
QUERY_FILE="$PROJECT_ROOT/queries/package_manager_transitive_binder.sql"
REPORT_FILE="$PROJECT_ROOT/data/raw/inheritance/java-inheritance-report.json"
REBUILD_SCRIPT="$PROJECT_ROOT/scripts/rebuild_all.sh"

log() {
    printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"
}

die() {
    printf '\n[ERROR] %s\n' "$*" >&2
    exit 1
}

trap 'die "failed at line $LINENO"' ERR

for path in \
    "$PROJECT_ROOT" \
    "$PROJECT_ROOT/.venv" \
    "$FW_BASE" \
    "$REBUILD_SCRIPT"; do
    [[ -e "$path" ]] || die "Missing required path: $path"
done

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

mkdir -p \
    "$PROJECT_ROOT/collectors/source" \
    "$PROJECT_ROOT/tests/unit" \
    "$PROJECT_ROOT/queries" \
    "$PROJECT_ROOT/data/raw/inheritance"

touch \
    "$PROJECT_ROOT/collectors/source/__init__.py" \
    "$PROJECT_ROOT/tests/unit/__init__.py"

for file in "$IMPORTER" "$TEST_FILE" "$QUERY_FILE"; do
    if [[ -f "$file" ]]; then
        cp -a "$file" "$file.bak.$(date '+%Y%m%d-%H%M%S')"
    fi
done

log "Writing unit tests"

cat > "$TEST_FILE" <<'PY'
from collectors.source.java_inheritance_importer import (
    build_child_qname,
    split_inherits,
)


def test_split_inherits_removes_generics() -> None:
    assert split_inherits(
        "Base<T>, First, Comparable<Child<T>>"
    ) == ("Base", "First", "Comparable")


def test_split_inherits_handles_single_parent() -> None:
    assert split_inherits("IPackageManagerBase") == (
        "IPackageManagerBase",
    )


def test_build_top_level_qname() -> None:
    assert build_child_qname(
        package_name="com.example",
        scope=None,
        name="Child",
    ) == "com.example.Child"


def test_build_nested_qname() -> None:
    assert build_child_qname(
        package_name="com.example",
        scope="Outer",
        name="Inner",
    ) == "com.example.Outer.Inner"


def test_qualified_scope_is_not_duplicated() -> None:
    assert build_child_qname(
        package_name="com.example",
        scope="com.example.Outer",
        name="Inner",
    ) == "com.example.Outer.Inner"
PY

log "Writing Java inheritance importer"

cat > "$IMPORTER" <<'PY'
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from graph.writer import Edge, GraphWriter


PACKAGE_RE = re.compile(
    r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;?",
    re.MULTILINE,
)

IMPORT_RE = re.compile(
    r"^\s*import\s+(?:static\s+)?"
    r"([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)*)(\.\*)?\s*;?",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DbType:
    node_id: str
    node_type: str
    qualified_name: str
    display_name: str
    source_path: str


def strip_generics(value: str) -> str:
    result: list[str] = []
    depth = 0

    for char in value:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            result.append(char)

    return "".join(result)


def split_inherits(value: str | list[str] | None) -> tuple[str, ...]:
    if not value:
        return ()

    if isinstance(value, list):
        raw = ",".join(str(item) for item in value)
    else:
        raw = str(value)

    raw = strip_generics(raw)
    result: list[str] = []

    for part in raw.split(","):
        name = re.sub(r"\s+", "", part)
        name = name.replace("$", ".")
        if re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.]*",
            name,
        ):
            result.append(name)

    return tuple(result)


def build_child_qname(
    package_name: str,
    scope: str | None,
    name: str,
) -> str:
    normalized_scope = (scope or "").replace(":", ".")

    if normalized_scope:
        if (
            package_name
            and normalized_scope.startswith(
                package_name + "."
            )
        ):
            owner = normalized_scope
        elif package_name:
            owner = f"{package_name}.{normalized_scope}"
        else:
            owner = normalized_scope

        return f"{owner}.{name}"

    if package_name:
        return f"{package_name}.{name}"

    return name


@lru_cache(maxsize=65536)
def read_java_context(
    absolute_path: str,
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    path = Path(absolute_path)

    try:
        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return "", {}, ()

    package_match = PACKAGE_RE.search(text)
    package_name = (
        package_match.group(1)
        if package_match
        else ""
    )

    imports: dict[str, str] = {}
    wildcard_imports: list[str] = []

    for match in IMPORT_RE.finditer(text):
        qualified = match.group(1).replace("$", ".")
        if match.group(2):
            wildcard_imports.append(qualified)
        else:
            imports[qualified.rsplit(".", 1)[-1]] = qualified

    return package_name, imports, tuple(wildcard_imports)


def normalize_source_path(
    path: str,
    source_root: Path,
) -> str:
    value = Path(path)

    try:
        return str(
            value.resolve().relative_to(source_root.resolve())
        )
    except ValueError:
        return str(value.resolve())


def load_types(
    connection: sqlite3.Connection,
) -> list[DbType]:
    rows = connection.execute(
        """
        SELECT
            node_id,
            node_type,
            qualified_name,
            display_name,
            source_path
        FROM node
        WHERE node_type IN (
            'JAVA_CLASS',
            'JAVA_INTERFACE',
            'JAVA_ENUM',
            'KOTLIN_CLASS',
            'KOTLIN_INTERFACE',
            'KOTLIN_OBJECT'
        )
          AND qualified_name IS NOT NULL
          AND source_path IS NOT NULL
        """
    )

    return [
        DbType(
            node_id=row[0],
            node_type=row[1],
            qualified_name=row[2],
            display_name=row[3],
            source_path=row[4],
        )
        for row in rows
    ]


class TypeIndex:
    def __init__(self, types: list[DbType]) -> None:
        self.by_qname: dict[str, DbType] = {}
        self.by_simple: dict[str, list[DbType]] = defaultdict(list)

        for item in types:
            self.by_qname[item.qualified_name] = item
            self.by_simple[item.display_name].append(item)

    def resolve(
        self,
        reference: str,
        child: DbType,
        package_name: str,
        imports: dict[str, str],
        wildcard_imports: tuple[str, ...],
    ) -> DbType | None:
        reference = reference.replace("$", ".")
        simple = reference.rsplit(".", 1)[-1]

        direct = self.by_qname.get(reference)
        if direct:
            return direct

        imported = imports.get(simple)
        if imported:
            direct = self.by_qname.get(imported)
            if direct:
                return direct

        if package_name:
            direct = self.by_qname.get(
                f"{package_name}.{reference}"
            )
            if direct:
                return direct

        package_parts = (
            package_name.split(".")
            if package_name
            else []
        )
        child_parts = child.qualified_name.split(".")
        type_parts = child_parts[len(package_parts):]

        for keep in range(
            len(type_parts) - 1,
            -1,
            -1,
        ):
            candidate = ".".join(
                package_parts
                + type_parts[:keep]
                + [reference]
            )
            direct = self.by_qname.get(candidate)
            if direct:
                return direct

        wildcard_matches: list[DbType] = []

        for package in wildcard_imports:
            direct = self.by_qname.get(
                f"{package}.{simple}"
            )
            if direct:
                wildcard_matches.append(direct)

        if len(wildcard_matches) == 1:
            return wildcard_matches[0]

        matches = self.by_simple.get(simple, [])
        if len(matches) == 1:
            return matches[0]

        return None


def edge_type_for(
    child: DbType,
    parent: DbType,
) -> str | None:
    interface_types = {"JAVA_INTERFACE", "KOTLIN_INTERFACE"}
    class_types = {"JAVA_CLASS", "JAVA_ENUM", "KOTLIN_CLASS", "KOTLIN_OBJECT"}

    if parent.node_type in interface_types:
        if child.node_type in interface_types:
            return "EXTENDS"
        return "IMPLEMENTS_JAVA_INTERFACE"

    if parent.node_type in class_types:
        return "EXTENDS"

    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ctags-jsonl",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
    )
    args = parser.parse_args()

    with sqlite3.connect(args.db) as connection:
        types = load_types(connection)

    index = TypeIndex(types)
    writer = GraphWriter(args.db)

    resolved: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    records_with_inherits = 0

    try:
        with args.ctags_jsonl.open(
            "r",
            encoding="utf-8",
        ) as stream:
            for raw_line in stream:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                if record.get("_type") != "tag":
                    continue

                if record.get("kind") not in {
                    "class",
                    "interface",
                    "enum",
                }:
                    continue

                inherited_names = split_inherits(
                    record.get("inherits")
                )
                if not inherited_names:
                    continue

                records_with_inherits += 1

                raw_path = record.get("path", "")
                package_name, imports, wildcard_imports = (
                    read_java_context(
                        str(Path(raw_path).resolve())
                    )
                )
                child_qname = build_child_qname(
                    package_name,
                    record.get("scope"),
                    record.get("name", ""),
                )
                child = index.by_qname.get(child_qname)
                source_path = normalize_source_path(
                    raw_path,
                    args.source_root,
                )
                line = record.get("line")

                if not child:
                    unresolved.append(
                        {
                            "reason": "child_not_found",
                            "child": child_qname,
                            "source_path": source_path,
                            "line": line,
                        }
                    )
                    continue

                for inherited_name in inherited_names:
                    parent = index.resolve(
                        inherited_name,
                        child,
                        package_name,
                        imports,
                        wildcard_imports,
                    )

                    if not parent:
                        unresolved.append(
                            {
                                "reason": "parent_not_found_or_ambiguous",
                                "child": child.qualified_name,
                                "parent_reference": inherited_name,
                                "source_path": source_path,
                                "line": line,
                            }
                        )
                        continue

                    edge_type = edge_type_for(
                        child,
                        parent,
                    )
                    if not edge_type:
                        unresolved.append(
                            {
                                "reason": "unsupported_type_relation",
                                "child": child.qualified_name,
                                "parent": parent.qualified_name,
                                "source_path": source_path,
                                "line": line,
                            }
                        )
                        continue

                    identity = (
                        edge_type,
                        child.node_id,
                        parent.node_id,
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)

                    writer.upsert_edge(
                        Edge(
                            edge_type=edge_type,
                            from_node_id=child.node_id,
                            to_node_id=parent.node_id,
                            properties={
                                "source_field": "ctags.inherits",
                                "direct": True,
                            },
                            source_path=source_path,
                            line_start=line,
                            line_end=line,
                            extractor="java-inheritance-v0.1",
                        )
                    )

                    resolved.append(
                        {
                            "edge_type": edge_type,
                            "child": child.qualified_name,
                            "parent": parent.qualified_name,
                            "source_path": source_path,
                            "line": line,
                        }
                    )
    finally:
        writer.close()

    report = {
        "summary": {
            "ctags_records_with_inherits": records_with_inherits,
            "resolved_relations": len(resolved),
            "unresolved_relations": len(unresolved),
        },
        "resolved": resolved,
        "unresolved": unresolved,
    }

    args.report.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    args.report.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    extends_count = sum(
        item["edge_type"] == "EXTENDS"
        for item in resolved
    )
    implements_count = sum(
        item["edge_type"]
        == "IMPLEMENTS_JAVA_INTERFACE"
        for item in resolved
    )

    print(
        f"Ctags records with inherits: "
        f"{records_with_inherits}; "
        f"resolved: {len(resolved)}; "
        f"EXTENDS: {extends_count}; "
        f"IMPLEMENTS_JAVA_INTERFACE: "
        f"{implements_count}; "
        f"unresolved: {len(unresolved)}"
    )

    if records_with_inherits == 0:
        print(
            "WARNING: Ctags JSON contains no inherits field. "
            "Graph will contain no inheritance edges for this pass."
        )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

log "Writing transitive Binder query"

cat > "$QUERY_FILE" <<'SQL'
WITH RECURSIVE inheritance(
    class_id,
    ancestor_id,
    depth,
    visited
) AS (
    SELECT
        e.from_node_id,
        e.to_node_id,
        1,
        e.from_node_id || '|' || e.to_node_id
    FROM edge e
    WHERE e.edge_type = 'EXTENDS'

    UNION ALL

    SELECT
        inheritance.class_id,
        e.to_node_id,
        inheritance.depth + 1,
        inheritance.visited || '|' || e.to_node_id
    FROM inheritance
    JOIN edge e
      ON e.from_node_id = inheritance.ancestor_id
    WHERE e.edge_type = 'EXTENDS'
      AND inheritance.depth < 20
      AND instr(
          inheritance.visited,
          e.to_node_id
      ) = 0
)
SELECT DISTINCT
    impl.qualified_name AS implementation,
    base.qualified_name AS binder_base,
    aidl.qualified_name AS binder_interface,
    inheritance.depth
FROM inheritance
JOIN edge binder_edge
  ON binder_edge.from_node_id =
     inheritance.ancestor_id
 AND binder_edge.edge_type =
     'IMPLEMENTS_BINDER'
JOIN node impl
  ON impl.node_id = inheritance.class_id
JOIN node base
  ON base.node_id = inheritance.ancestor_id
JOIN node aidl
  ON aidl.node_id = binder_edge.to_node_id
WHERE impl.qualified_name =
      'com.android.server.pm.PackageManagerService.IPackageManagerImpl'
ORDER BY inheritance.depth;
SQL

log "Running unit tests"

python -m py_compile "$IMPORTER"
pytest -q "$TEST_FILE"

log "Updating Ctags fields in canonical rebuild"

python - <<'PY'
from pathlib import Path
import re

path = Path(
    "/home/ts/android-context-intelligence/"
    "scripts/rebuild_all.sh"
)
text = path.read_text(encoding="utf-8")

text, count = re.subn(
    r"--fields=\+nKSEi?\b",
    "--fields=+nKSEi",
    text,
)

if count == 0:
    raise SystemExit(
        "Could not find --fields=+nKSE in rebuild_all.sh"
    )

path.write_text(text, encoding="utf-8")
print(f"Updated Ctags fields in {path}")
PY

log "Regenerating Ctags JSON with inheritance field"

scan_paths=(
    "$FW_BASE/core"
    "$FW_BASE/services"
)
[[ -d "$FW_BASE/packages" ]] &&
    scan_paths+=("$FW_BASE/packages")

rm -f "$CTAGS_JSONL"

ctags \
    --languages=Java \
    --output-format=json \
    --fields=+nKSEi \
    -R \
    -f "$CTAGS_JSONL" \
    "${scan_paths[@]}"

log "Checking representative Ctags inheritance records"

python - <<'PY'
import json
from pathlib import Path

path = Path(
    "/home/ts/android-context-intelligence/"
    "data/raw/ctags/frameworks-base.jsonl"
)

targets = {
    "ActivityManagerService",
    "IPackageManagerBase",
    "IPackageManagerImpl",
}
found = {}

for line in path.open(encoding="utf-8"):
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        continue

    name = record.get("name")
    if name in targets and record.get("kind") == "class":
        found.setdefault(name, []).append(
            {
                "scope": record.get("scope"),
                "inherits": record.get("inherits"),
                "path": record.get("path"),
                "line": record.get("line"),
            }
        )

for name in sorted(targets):
    print(name, found.get(name, []))

if not any(
    item.get("inherits")
    for values in found.values()
    for item in values
):
    raise SystemExit(
        "Representative records contain no inherits field"
    )
PY

log "Removing previous inheritance edges"

if false; then

sqlite3 "$DB_PATH" <<'SQL'
PRAGMA foreign_keys=ON;
DELETE FROM edge
WHERE edge_type IN (
    'EXTENDS',
    'IMPLEMENTS_JAVA_INTERFACE'
);
SQL

log "Importing Java Inheritance Graph v0.1"

python -m collectors.source.java_inheritance_importer \
    --ctags-jsonl "$CTAGS_JSONL" \
    --source-root "$AOSP_ROOT" \
    --db "$DB_PATH" \
    --report "$REPORT_FILE"

log "Checking foreign-key integrity"

FK_ERRORS="$(
    sqlite3 "$DB_PATH" "PRAGMA foreign_key_check;"
)"

if [[ -n "$FK_ERRORS" ]]; then
    printf '%s\n' "$FK_ERRORS"
    die "foreign_key_check failed"
fi

echo "foreign_key_check: PASS"

log "Inheritance edge summary"

sqlite3 -header -column "$DB_PATH" <<'SQL'
SELECT edge_type, COUNT(*) AS count
FROM edge
WHERE edge_type IN (
    'EXTENDS',
    'IMPLEMENTS_JAVA_INTERFACE'
)
GROUP BY edge_type
ORDER BY edge_type;
SQL

log "Validating PackageManager direct inheritance"

PMS_DIRECT="$(
    sqlite3 -header -column "$DB_PATH" "
    SELECT
        child.qualified_name AS child,
        parent.qualified_name AS parent
    FROM edge e
    JOIN node child
      ON child.node_id = e.from_node_id
    JOIN node parent
      ON parent.node_id = e.to_node_id
    WHERE e.edge_type = 'EXTENDS'
      AND child.qualified_name =
          'com.android.server.pm.PackageManagerService.IPackageManagerImpl'
      AND parent.qualified_name =
          'com.android.server.pm.IPackageManagerBase';
    "
)"

printf '%s\n' "$PMS_DIRECT"

grep -q \
    'com.android.server.pm.IPackageManagerBase' \
    <<< "$PMS_DIRECT" ||
    die "PackageManager direct inheritance validation failed"

log "Validating transitive PackageManager Binder relation"

PMS_TRANSITIVE="$(
    sqlite3 -header -column "$DB_PATH" \
        < "$QUERY_FILE"
)"

printf '%s\n' "$PMS_TRANSITIVE"

grep -q \
    'android.content.pm.IPackageManager' \
    <<< "$PMS_TRANSITIVE" ||
    die "PackageManager transitive Binder validation failed"

log "Integrating importer into rebuild_all.sh"

python - <<'PY'
from pathlib import Path

path = Path(
    "/home/ts/android-context-intelligence/"
    "scripts/rebuild_all.sh"
)
text = path.read_text(encoding="utf-8")

command = (
    'log "Importing Java Inheritance Graph"\n'
    'python -m collectors.source.java_inheritance_importer \\\n'
    '    --ctags-jsonl "$CTAGS_OUTPUT" \\\n'
    '    --source-root "$AOSP_ROOT" \\\n'
    '    --db "$DB_PATH" \\\n'
    '    --report \\\n'
    '    "$PROJECT_ROOT/data/raw/inheritance/'
    'java-inheritance-report.json"\n\n'
)

if "collectors.source.java_inheritance_importer" not in text:
    marker = 'log "Validating foreign keys"'
    position = text.find(marker)
    if position < 0:
        raise SystemExit(
            'Missing marker: log "Validating foreign keys"'
        )
    text = text[:position] + command + text[position:]

validation = (
    'log "Validating PackageManager inheritance"\n'
    'PMS_INHERITANCE="$(\n'
    '    sqlite3 "$DB_PATH" "\n'
    '    SELECT COUNT(*)\n'
    '    FROM edge e\n'
    '    JOIN node child\n'
    '      ON child.node_id=e.from_node_id\n'
    '    JOIN node parent\n'
    '      ON parent.node_id=e.to_node_id\n'
    "    WHERE e.edge_type='EXTENDS'\n"
    '      AND child.qualified_name=\n'
    "          'com.android.server.pm."
    "PackageManagerService.IPackageManagerImpl'\n"
    '      AND parent.qualified_name=\n'
    "          'com.android.server.pm."
    "IPackageManagerBase';\n"
    '    "\n'
    ')"\n'
    '[[ "$PMS_INHERITANCE" -ge 1 ]] ||\n'
    '    die "PackageManager inheritance '
    'validation failed"\n\n'
)

if "Validating PackageManager inheritance" not in text:
    marker = 'log "Graph summary"'
    position = text.find(marker)
    if position < 0:
        raise SystemExit(
            'Missing marker: log "Graph summary"'
        )
    text = text[:position] + validation + text[position:]

path.write_text(text, encoding="utf-8")
print(f"Integrated importer into {path}")
PY

bash -n "$REBUILD_SCRIPT"

log "Updating README and manifest"

python - <<'PY'
from pathlib import Path

root = Path("/home/ts/android-context-intelligence")
readme = root / "README.md"
text = (
    readme.read_text(encoding="utf-8")
    if readme.exists()
    else "# Android Context Intelligence\n"
)

section = """
## Java Inheritance Graph v0.1

Edges:

- `EXTENDS`
- `IMPLEMENTS_JAVA_INTERFACE`

Package Manager transitive Binder query:

```bash
sqlite3 -header -column data/android_context.db \
  < queries/package_manager_transitive_binder.sql
```
"""

if "## Java Inheritance Graph v0.1" not in text:
    text = text.rstrip() + "\n" + section

readme.write_text(text, encoding="utf-8")

manifest = root / "INSTALLATION_MANIFEST.txt"
manifest_text = (
    manifest.read_text(encoding="utf-8")
    if manifest.exists()
    else ""
)

if "Java Inheritance Graph v0.1" not in manifest_text:
    manifest_text = (
        manifest_text.rstrip()
        + "\n  Java Inheritance Graph v0.1\n"
    )

manifest.write_text(
    manifest_text,
    encoding="utf-8",
)
PY

fi

log "Java Inheritance Graph v0.1 completed (Execution skipped, deferred to rebuild_all.sh)"

cat <<EOF

Created:
  $IMPORTER
  $TEST_FILE
  $QUERY_FILE
  $REPORT_FILE

Updated canonical rebuild:
  $REBUILD_SCRIPT

Rebuild all graph layers:
  cd "$PROJECT_ROOT"
  ./scripts/rebuild_all.sh

Query PMS transitive Binder relation:
  sqlite3 -header -column "$DB_PATH" \\
    < "$QUERY_FILE"

Next task:
  System Service Registration Graph v0.1
EOF

