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
