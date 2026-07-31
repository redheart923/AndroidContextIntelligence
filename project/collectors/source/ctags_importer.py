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


def parse_line_range(
    record: dict[str, object],
) -> tuple[int | None, int | None]:
    start = record.get("line")
    if not isinstance(start, int):
        return None, None
    raw_end = record.get("end")
    end = raw_end if isinstance(raw_end, int) and raw_end >= start else start
    return start, end


def _mask_non_code(text: str) -> str:
    """Mask comments and literals while preserving offsets and newlines."""
    result = list(text)
    index = 0
    state = "code"
    block_depth = 0

    def mask(position: int) -> None:
        if result[position] != "\n":
            result[position] = " "

    while index < len(text):
        pair = text[index:index + 2]
        triple = text[index:index + 3]

        if state == "code":
            if pair == "//":
                mask(index); mask(index + 1)
                index += 2; state = "line_comment"
                continue
            if pair == "/*":
                mask(index); mask(index + 1)
                index += 2; state = "block_comment"; block_depth = 1
                continue
            if triple == '\"\"\"':
                for position in range(index, index + 3):
                    mask(position)
                index += 3; state = "triple_string"
                continue
            if text[index] == '"':
                mask(index); index += 1; state = "string"
                continue
            if text[index] == "'":
                mask(index); index += 1; state = "character"
                continue
            index += 1
            continue

        if state == "line_comment":
            if text[index] == "\n":
                state = "code"
            else:
                mask(index)
            index += 1
            continue

        if state == "block_comment":
            if pair == "/*":
                mask(index); mask(index + 1)
                index += 2; block_depth += 1
                continue
            if pair == "*/":
                mask(index); mask(index + 1)
                index += 2; block_depth -= 1
                if block_depth == 0:
                    state = "code"
                continue
            mask(index); index += 1
            continue

        if state == "triple_string":
            if triple == '\"\"\"':
                for position in range(index, index + 3):
                    mask(position)
                index += 3; state = "code"
                continue
            mask(index); index += 1
            continue

        if text[index] == "\\" and index + 1 < len(text):
            mask(index); mask(index + 1)
            index += 2
            continue

        delimiter = '"' if state == "string" else "'"
        if text[index] == delimiter:
            mask(index); index += 1; state = "code"
            continue
        mask(index); index += 1

    return "".join(result)


@lru_cache(maxsize=65536)
def _read_masked_source(absolute_path: str) -> str | None:
    try:
        text = Path(absolute_path).read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    return _mask_non_code(text)


def _infer_balanced_method_end(record: dict[str, object], start: int) -> int | None:
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or record.get("name") == "<lambda>":
        return None

    masked = _read_masked_source(str(Path(raw_path).resolve()))
    if masked is None:
        return None

    line_offsets = [0]
    for match in re.finditer("\n", masked):
        line_offsets.append(match.end())
    if start < 1 or start > len(line_offsets):
        return None

    index = line_offsets[start - 1]
    parameter_depth = 0
    square_depth = 0
    saw_parameters = False
    parameters_closed = False
    body_start: int | None = None

    while index < len(masked):
        character = masked[index]
        if character == "(":
            parameter_depth += 1
            saw_parameters = True
        elif character == ")" and parameter_depth:
            parameter_depth -= 1
            if saw_parameters and parameter_depth == 0:
                parameters_closed = True
        elif character == "[":
            square_depth += 1
        elif character == "]" and square_depth:
            square_depth -= 1
        elif parameters_closed and parameter_depth == 0 and square_depth == 0:
            if character == "{":
                body_start = index
                break
            if character in "=;":
                return None
        index += 1

    if body_start is None:
        return None

    brace_depth = 0
    for index in range(body_start, len(masked)):
        character = masked[index]
        if character == "{":
            brace_depth += 1
        elif character == "}":
            brace_depth -= 1
            if brace_depth == 0:
                return masked.count("\n", 0, index) + 1
    return None


def resolve_line_range(
    record: dict[str, object],
) -> tuple[int | None, int | None]:
    start, end = parse_line_range(record)
    if start is None:
        return None, None
    raw_end = record.get("end")
    if isinstance(raw_end, int) and raw_end >= start:
        return start, raw_end
    if record.get("kind") == "method":
        inferred_end = _infer_balanced_method_end(record, start)
        if inferred_end is not None and inferred_end >= start:
            return start, inferred_end
    return start, end


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
    repository: str = "unknown",
    source_revision: str = "unknown",
) -> tuple[int, int]:
    active_kind_map = kind_map if kind_map is not None else KIND_MAP

    writer = GraphWriter(db_path, source_revision=source_revision)
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

            line_start, line_end = resolve_line_range(record)
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
                line_start=line_start,
                line_end=line_end,
                extractor="universal-ctags-v0.2.1",
            )
            writer.upsert_node(node)
            writer.upsert_symbol_definition(
                node,
                repository=repository,
                source_path=source_path,
                line_start=line_start,
                line_end=line_end,
            )

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
                    line_start=line_start,
                    line_end=line_end,
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
    source_revision: str = "unknown",
) -> tuple[int, int]:
    active_kind_map = kind_map if kind_map is not None else KIND_MAP
    active_owner_kind_map = owner_kind_map if owner_kind_map is not None else OWNER_KIND_MAP
    owner_ids = collect_owner_ids(db_path, active_owner_kind_map)
    writer = GraphWriter(db_path, source_revision=source_revision)
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
            line_start, line_end = resolve_line_range(record)

            writer.upsert_edge(
                Edge(
                    edge_type=relation,
                    from_node_id=owner_id,
                    to_node_id=member_id,
                    source_path=source_path,
                    line_start=line_start,
                    line_end=line_end,
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
    parser.add_argument("--repository", default="unknown")
    parser.add_argument("--source-revision", default="unknown")
    args = parser.parse_args()

    kind_map = KOTLIN_KIND_MAP if args.language == "kotlin" else KIND_MAP
    owner_kind_map = KOTLIN_OWNER_KIND_MAP if args.language == "kotlin" else OWNER_KIND_MAP

    imported, skipped = first_pass(
        args.ctags_jsonl,
        args.db_path,
        args.source_root,
        kind_map=kind_map,
        repository=args.repository,
        source_revision=args.source_revision,
    )
    owner_edges, missing_owner = second_pass(
        args.ctags_jsonl,
        args.db_path,
        args.source_root,
        kind_map=kind_map,
        owner_kind_map=owner_kind_map,
        source_revision=args.source_revision,
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
