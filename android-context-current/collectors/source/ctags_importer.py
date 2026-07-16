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
