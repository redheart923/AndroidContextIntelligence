from __future__ import annotations

import argparse
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

KOTLIN_KIND_MAP = {
    "class": "KOTLIN_CLASS",
    "interface": "KOTLIN_INTERFACE",
    "object": "KOTLIN_OBJECT",
    "typealias": "KOTLIN_TYPEALIAS",
    "method": "KOTLIN_METHOD",
    "variable": "KOTLIN_VARIABLE",
    "property": "KOTLIN_PROPERTY",
    "constant": "KOTLIN_CONSTANT",
    "enumConstant": "KOTLIN_ENUM_CONSTANT",
    "package": "KOTLIN_PACKAGE",
}

OWNER_KIND_MAP = {
    "class": "JAVA_CLASS",
    "interface": "JAVA_INTERFACE",
    "enum": "JAVA_ENUM",
    "annotation": "JAVA_ANNOTATION",
    "package": "JAVA_PACKAGE",
}

KOTLIN_OWNER_KIND_MAP = {
    "class": "KOTLIN_CLASS",
    "enum": "KOTLIN_CLASS",
    "interface": "KOTLIN_INTERFACE",
    "object": "KOTLIN_OBJECT",
    "package": "KOTLIN_PACKAGE",
}

PACKAGE_RE = re.compile(
    r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;?",
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
    node_type: str,
) -> tuple[str, str | None]:
    name = record.get("name", "")
    signature = record.get("signature") or ""
    scope = normalize_scope(record.get("scope"), package_name)

    if node_type in {"JAVA_PACKAGE", "KOTLIN_PACKAGE"}:
        # Ctags may emit only the final segment as the name.
        return package_name or name, None

    if node_type in {"JAVA_CLASS", "JAVA_INTERFACE", "JAVA_ENUM", "JAVA_ANNOTATION", "KOTLIN_CLASS", "KOTLIN_INTERFACE", "KOTLIN_OBJECT", "KOTLIN_TYPEALIAS"}:
        if scope:
            qualified_name = f"{scope}.{name}"
        else:
            qualified_name = qualify(package_name, name)
        return qualified_name, scope or package_name or None

    if node_type in {"JAVA_METHOD", "KOTLIN_METHOD"}:
        owner = scope or package_name
        qualified_name = (
            f"{owner}#{name}{signature}"
            if owner
            else f"{name}{signature}"
        )
        return qualified_name, owner or None

    if node_type in {"JAVA_FIELD", "JAVA_ENUM_CONSTANT", "KOTLIN_VARIABLE", "KOTLIN_CONSTANT", "KOTLIN_PROPERTY", "KOTLIN_ENUM_CONSTANT"}:
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


def collect_owner_ids(db_path: Path, owner_kind_map: dict[str, str]) -> set[str]:
    node_types = tuple(owner_kind_map.values())
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
    kind_map: dict[str, str] | None = None,
) -> tuple[int, int]:
    active_kind_map = kind_map if kind_map is not None else KIND_MAP

    writer = GraphWriter(db_path)
    imported = 0
    skipped = 0

    try:
        for record in iter_records(input_path):
            kind = record.get("kind")
            node_type = active_kind_map.get(kind)
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
                node_type,
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
    kind_map: dict[str, str] | None = None,
    owner_kind_map: dict[str, str] | None = None,
) -> tuple[int, int]:
    active_kind_map = kind_map if kind_map is not None else KIND_MAP
    active_owner_kind_map = owner_kind_map if owner_kind_map is not None else OWNER_KIND_MAP
    owner_ids = collect_owner_ids(db_path, active_owner_kind_map)
    writer = GraphWriter(db_path)
    inserted = 0
    missing_owner = 0

    try:
        for record in iter_records(input_path):
            kind = record.get("kind")
            member_kinds = {"method", "field", "enumConstant"}
            if active_kind_map is KOTLIN_KIND_MAP:
                member_kinds = {"method", "variable", "constant", "property", "enumConstant"}
            if kind not in member_kinds:
                continue

            scope_kind = record.get("scopeKind") or ""
            if not scope_kind:
                scope_kind = "package"

            owner_type = active_owner_kind_map.get(scope_kind)
            if not owner_type:
                missing_owner += 1
                continue

            raw_path = record.get("path", "")
            source_path = normalize_path(raw_path, source_root)
            package_name = read_package_name(
                str(Path(raw_path).resolve())
            )
            node_type = active_kind_map.get(kind)
            if not node_type:
                missing_owner += 1
                continue

            qualified_name, owner_qname = build_identity(
                record,
                package_name,
                node_type,
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
    parser = argparse.ArgumentParser(description="Import Ctags JSONL into SQLite graph.")
    parser.add_argument("ctags_jsonl", type=Path)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--language", choices=["java", "kotlin"], default="java")
    args = parser.parse_args()

    kind_map = KOTLIN_KIND_MAP if args.language == "kotlin" else KIND_MAP
    owner_kind_map = KOTLIN_OWNER_KIND_MAP if args.language == "kotlin" else OWNER_KIND_MAP

    imported, skipped = first_pass(
        args.ctags_jsonl,
        args.db_path,
        args.source_root,
        kind_map=kind_map,
    )
    owner_edges, missing_owner = second_pass(
        args.ctags_jsonl,
        args.db_path,
        args.source_root,
        kind_map=kind_map,
        owner_kind_map=owner_kind_map,
    )

    print(
        f"Imported {imported} {args.language} symbols; "
        f"skipped {skipped}; "
        f"owner edges {owner_edges}; "
        f"unresolved owners {missing_owner}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
