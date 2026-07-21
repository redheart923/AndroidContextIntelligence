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
