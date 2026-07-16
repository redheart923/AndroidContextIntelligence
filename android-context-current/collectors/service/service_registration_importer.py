from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from graph.writer import Edge, GraphWriter, Node, stable_id


PACKAGE_RE = re.compile(
    r"\bpackage\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;"
)

IMPORT_RE = re.compile(
    r"\bimport\s+(?:static\s+)?"
    r"([A-Za-z_][A-Za-z0-9_.$]*)(\.\*)?\s*;"
)

CLASS_RE = re.compile(
    r"\b(class|interface|enum)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*<[^>{;]*>)?"
    r"[^;{}]*\{",
    re.DOTALL,
)

STRING_CONSTANT_RE = re.compile(
    r"""
    (?:
        public|protected|private|static|final|
        transient|volatile|\s
    )*
    \bString\s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    \s*=\s*
    (?P<expression>[^;]+)
    ;
    """,
    re.VERBOSE,
)

VARIABLE_DECL_RE = re.compile(
    r"""
    (?P<type>
        [A-Za-z_][A-Za-z0-9_.$]*
        (?:\s*<[^;=(){}]+>)?
        (?:\[\])?
    )
    \s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    \s*=
    """,
    re.VERBOSE,
)

METHOD_DECL_RE = re.compile(
    r"""
    (?:
        public|protected|private|static|final|abstract|
        synchronized|native|strictfp|default|\s
    )+
    (?P<return_type>[A-Za-z_][A-Za-z0-9_.$<>\[\]?]*)
    \s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    \s*\(
    """,
    re.VERBOSE,
)

STRING_LITERAL_RE = re.compile(
    r'^"((?:\\.|[^"\\])*)"$',
    re.DOTALL,
)

CALL_PATTERNS = (
    (
        "ServiceManager.addService",
        re.compile(r"\bServiceManager\s*\.\s*addService\s*\("),
        "binder",
    ),
    (
        "publishBinderService",
        re.compile(r"\bpublishBinderService\s*\("),
        "binder",
    ),
    (
        "LocalServices.addService",
        re.compile(r"\bLocalServices\s*\.\s*addService\s*\("),
        "local",
    ),
)

TEST_PATH_PARTS = {
    "test",
    "tests",
    "testing",
    "unittest",
    "unittests",
    "mock",
    "mocks",
    "benchmark",
    "benchmarks",
}


@dataclass(frozen=True)
class ClassRange:
    name: str
    qualified_name: str
    start: int
    body_start: int
    end: int


@dataclass(frozen=True)
class RegistrationCall:
    api: str
    service_kind: str
    key_expression: str
    instance_expression: str
    offset: int
    line: int
    raw_call: str


@dataclass(frozen=True)
class RegistrationFact:
    registration_id: str
    api: str
    service_kind: str
    key_expression: str
    resolved_key: str | None
    instance_expression: str
    resolved_instance_type: str | None
    resolution_status: str
    source_path: str
    line: int
    raw_call: str
    is_test_source: bool


@dataclass(frozen=True)
class DbType:
    node_id: str
    node_type: str
    qualified_name: str
    display_name: str


def remove_comments_preserving_layout(text: str) -> str:
    output: list[str] = []
    index = 0
    length = len(text)
    state = "code"

    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""

        if state == "code":
            if char == "/" and nxt == "/":
                output.extend("  ")
                index += 2
                state = "line_comment"
                continue
            if char == "/" and nxt == "*":
                output.extend("  ")
                index += 2
                state = "block_comment"
                continue
            if char == '"':
                output.append(char)
                index += 1
                state = "string"
                continue
            if char == "'":
                output.append(char)
                index += 1
                state = "char"
                continue
            output.append(char)
            index += 1
            continue

        if state == "line_comment":
            if char == "\n":
                output.append("\n")
                state = "code"
            else:
                output.append(" ")
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and nxt == "/":
                output.extend("  ")
                index += 2
                state = "code"
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue

        if state in {"string", "char"}:
            output.append(char)
            if char == "\\" and index + 1 < length:
                output.append(text[index + 1])
                index += 2
                continue
            if state == "string" and char == '"':
                state = "code"
            elif state == "char" and char == "'":
                state = "code"
            index += 1

    return "".join(output)


def normalize_expression(expression: str) -> str:
    return " ".join(expression.strip().split())


def find_matching_delimiter(
    text: str,
    opening_index: int,
    opening: str,
    closing: str,
) -> int:
    depth = 0
    state = "code"
    index = opening_index

    while index < len(text):
        char = text[index]

        if state == "code":
            if char == '"':
                state = "string"
            elif char == "'":
                state = "char"
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == '"':
                state = "code"
        elif state == "char":
            if char == "\\":
                index += 1
            elif char == "'":
                state = "code"

        index += 1

    raise ValueError(
        f"Unclosed delimiter {opening}{closing} "
        f"at offset {opening_index}"
    )


def split_arguments(argument_text: str) -> list[str]:
    result: list[str] = []
    start = 0
    paren_depth = 0
    angle_depth = 0
    square_depth = 0
    brace_depth = 0
    state = "code"
    index = 0

    while index < len(argument_text):
        char = argument_text[index]

        if state == "code":
            if char == '"':
                state = "string"
            elif char == "'":
                state = "char"
            elif char == "(":
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
            elif char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)
            elif (
                char == ","
                and paren_depth == 0
                and angle_depth == 0
                and square_depth == 0
                and brace_depth == 0
            ):
                result.append(
                    normalize_expression(
                        argument_text[start:index]
                    )
                )
                start = index + 1
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == '"':
                state = "code"
        elif state == "char":
            if char == "\\":
                index += 1
            elif char == "'":
                state = "code"

        index += 1

    final = normalize_expression(argument_text[start:])
    if final:
        result.append(final)

    return result


def strip_generics(type_name: str) -> str:
    output: list[str] = []
    depth = 0

    for char in type_name:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            output.append(char)

    return "".join(output).replace("[]", "").strip()


def decode_java_string(value: str) -> str:
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return value


def is_test_path(path: str) -> bool:
    parts = {
        part.lower()
        for part in Path(path).parts
    }
    return bool(parts & TEST_PATH_PARTS)


class JavaSource:
    def __init__(
        self,
        path: Path,
        source_root: Path,
        text: str,
    ) -> None:
        self.path = path
        self.source_root = source_root
        self.text = text
        self.clean_text = remove_comments_preserving_layout(text)

        package_match = PACKAGE_RE.search(self.clean_text)
        self.package_name = (
            package_match.group(1)
            if package_match
            else ""
        )

        self.imports: dict[str, str] = {}
        self.wildcard_imports: list[str] = []

        for match in IMPORT_RE.finditer(self.clean_text):
            qualified = match.group(1).replace("$", ".")
            if match.group(2):
                self.wildcard_imports.append(qualified)
            else:
                self.imports[
                    qualified.rsplit(".", 1)[-1]
                ] = qualified

        self.class_ranges = self._parse_class_ranges()
        self.declared_qnames = {
            item.qualified_name
            for item in self.class_ranges
        }

    @classmethod
    def load(
        cls,
        path: Path,
        source_root: Path,
    ) -> "JavaSource":
        return cls(
            path,
            source_root,
            path.read_text(
                encoding="utf-8",
                errors="replace",
            ),
        )

    @property
    def source_path(self) -> str:
        try:
            return str(
                self.path.resolve().relative_to(
                    self.source_root.resolve()
                )
            )
        except ValueError:
            return str(self.path.resolve())

    def _parse_class_ranges(self) -> list[ClassRange]:
        temporary: list[
            tuple[str, int, int, int]
        ] = []

        for match in CLASS_RE.finditer(self.clean_text):
            opening = self.clean_text.find(
                "{",
                match.start(),
                match.end(),
            )
            if opening < 0:
                continue

            try:
                closing = find_matching_delimiter(
                    self.clean_text,
                    opening,
                    "{",
                    "}",
                )
            except ValueError:
                continue

            temporary.append(
                (
                    match.group(2),
                    match.start(),
                    opening,
                    closing,
                )
            )

        temporary.sort(key=lambda item: item[1])
        ranges: list[ClassRange] = []

        for name, start, opening, closing in temporary:
            parents = [
                item
                for item in ranges
                if item.body_start < start < item.end
            ]
            parent = (
                max(parents, key=lambda item: item.body_start)
                if parents
                else None
            )

            if parent:
                qualified = f"{parent.qualified_name}.{name}"
            elif self.package_name:
                qualified = f"{self.package_name}.{name}"
            else:
                qualified = name

            ranges.append(
                ClassRange(
                    name=name,
                    qualified_name=qualified,
                    start=start,
                    body_start=opening,
                    end=closing,
                )
            )

        return ranges

    def containing_class(
        self,
        offset: int,
    ) -> ClassRange | None:
        candidates = [
            item
            for item in self.class_ranges
            if item.body_start < offset < item.end
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: item.body_start,
        )

    def resolve_type_name(
        self,
        type_name: str,
        offset: int,
    ) -> str | None:
        type_name = strip_generics(type_name)
        type_name = type_name.replace("$", ".")

        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.]*",
            type_name,
        ):
            return None

        if "." in type_name:
            first = type_name.split(".", 1)[0]
            if first and first[0].islower():
                return type_name

        simple = type_name.rsplit(".", 1)[-1]
        imported = self.imports.get(simple)
        if imported:
            return imported

        top_level_candidate = (
            f"{self.package_name}.{type_name}"
            if self.package_name
            else type_name
        )
        if top_level_candidate in self.declared_qnames:
            return top_level_candidate

        containing = self.containing_class(offset)
        if containing:
            owner_parts = containing.qualified_name.split(".")
            package_parts = (
                self.package_name.split(".")
                if self.package_name
                else []
            )
            type_parts = owner_parts[len(package_parts):]

            for keep in range(
                len(type_parts),
                0,
                -1,
            ):
                candidate = ".".join(
                    package_parts
                    + type_parts[:keep]
                    + [type_name]
                )
                if candidate in self.declared_qnames:
                    return candidate

        if self.package_name:
            return top_level_candidate

        return type_name

    def resolve_instance_type(
        self,
        expression: str,
        offset: int,
    ) -> str | None:
        expression = normalize_expression(expression)

        if expression == "this":
            containing = self.containing_class(offset)
            return (
                containing.qualified_name
                if containing
                else None
            )

        match = re.search(
            r"\.\s*new\s+"
            r"([A-Za-z_][A-Za-z0-9_.$]*)"
            r"\s*\(",
            expression,
        )
        if match:
            containing = self.containing_class(offset)
            inner = match.group(1).replace("$", ".")
            if containing:
                candidate = (
                    f"{containing.qualified_name}.{inner}"
                )
                if candidate in self.declared_qnames:
                    return candidate
            return self.resolve_type_name(inner, offset)

        match = re.search(
            r"(?<!\.)\bnew\s+"
            r"([A-Za-z_][A-Za-z0-9_.$]*(?:<[^(){};]+>)?)"
            r"\s*\(",
            expression,
        )
        if match:
            return self.resolve_type_name(
                match.group(1),
                offset,
            )

        cast_match = re.match(
            r"^\(\s*([A-Za-z_][A-Za-z0-9_.$<>]*)\s*\)",
            expression,
        )
        if cast_match:
            return self.resolve_type_name(
                cast_match.group(1),
                offset,
            )

        if re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            expression,
        ):
            prefix = self.clean_text[:offset]
            matches = list(VARIABLE_DECL_RE.finditer(prefix))

            for match in reversed(matches):
                if match.group("name") == expression:
                    return self.resolve_type_name(
                        match.group("type"),
                        match.start(),
                    )

        method_match = re.match(
            r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            expression,
        )
        if method_match:
            method_name = method_match.group(1)
            prefix = self.clean_text[:offset]
            matches = [
                match
                for match in METHOD_DECL_RE.finditer(prefix)
                if match.group("name") == method_name
            ]
            if matches:
                return self.resolve_type_name(
                    matches[-1].group("return_type"),
                    matches[-1].start(),
                )

        return None


def find_registration_calls(
    source: JavaSource,
) -> list[RegistrationCall]:
    calls: list[RegistrationCall] = []

    for api, pattern, service_kind in CALL_PATTERNS:
        for match in pattern.finditer(source.clean_text):
            opening = source.clean_text.find(
                "(",
                match.start(),
                match.end(),
            )
            if opening < 0:
                continue

            try:
                closing = find_matching_delimiter(
                    source.clean_text,
                    opening,
                    "(",
                    ")",
                )
            except ValueError:
                continue

            arguments_text = source.clean_text[
                opening + 1 : closing
            ]
            arguments = split_arguments(arguments_text)

            if len(arguments) < 2:
                continue

            calls.append(
                RegistrationCall(
                    api=api,
                    service_kind=service_kind,
                    key_expression=arguments[0],
                    instance_expression=arguments[1],
                    offset=match.start(),
                    line=source.clean_text.count(
                        "\n",
                        0,
                        match.start(),
                    )
                    + 1,
                    raw_call=normalize_expression(
                        source.clean_text[
                            match.start() : closing + 1
                        ]
                    ),
                )
            )

    calls.sort(key=lambda item: item.offset)
    return calls


class ConstantResolver:
    def __init__(
        self,
        sources: Iterable[JavaSource],
    ) -> None:
        self.by_qualified: dict[
            str,
            tuple[str, JavaSource, int]
        ] = {}
        self.by_simple: dict[
            str,
            list[tuple[str, JavaSource, int]]
        ] = defaultdict(list)

        for source in sources:
            for match in STRING_CONSTANT_RE.finditer(
                source.clean_text
            ):
                containing = source.containing_class(
                    match.start()
                )
                if not containing:
                    continue

                name = match.group("name")
                expression = normalize_expression(
                    match.group("expression")
                )
                qualified = (
                    f"{containing.qualified_name}.{name}"
                )
                value = (
                    expression,
                    source,
                    match.start(),
                )
                self.by_qualified[qualified] = value
                self.by_simple[name].append(value)

    def resolve(
        self,
        expression: str,
        source: JavaSource,
        offset: int,
        depth: int = 0,
        visited: set[str] | None = None,
    ) -> str | None:
        if depth > 10:
            return None

        expression = normalize_expression(expression)
        literal = STRING_LITERAL_RE.match(expression)
        if literal:
            return decode_java_string(literal.group(1))

        if expression.startswith("(") and ")" in expression:
            expression = expression.split(")", 1)[1].strip()

        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.$]*",
            expression,
        ):
            return None

        reference = expression.replace("$", ".")
        visited = set() if visited is None else set(visited)

        if reference in visited:
            return None
        visited.add(reference)

        candidates: list[
            tuple[str, JavaSource, int]
        ] = []

        if reference in self.by_qualified:
            candidates.append(self.by_qualified[reference])

        parts = reference.split(".")
        simple = parts[-1]

        if len(parts) >= 2:
            owner_simple = parts[-2]
            imported_owner = source.imports.get(owner_simple)

            if imported_owner:
                qualified = f"{imported_owner}.{simple}"
                if qualified in self.by_qualified:
                    candidates.append(
                        self.by_qualified[qualified]
                    )

            if source.package_name:
                qualified = (
                    f"{source.package_name}.{reference}"
                )
                if qualified in self.by_qualified:
                    candidates.append(
                        self.by_qualified[qualified]
                    )

        containing = source.containing_class(offset)
        if containing:
            qualified = (
                f"{containing.qualified_name}.{simple}"
            )
            if qualified in self.by_qualified:
                candidates.append(
                    self.by_qualified[qualified]
                )

        imported_constant = source.imports.get(simple)
        if imported_constant:
            if imported_constant in self.by_qualified:
                candidates.append(
                    self.by_qualified[imported_constant]
                )

        simple_matches = self.by_simple.get(simple, [])
        if len(simple_matches) == 1:
            candidates.append(simple_matches[0])

        unique: list[
            tuple[str, JavaSource, int]
        ] = []
        seen_values: set[
            tuple[str, str, int]
        ] = set()

        for item in candidates:
            identity = (
                item[0],
                item[1].source_path,
                item[2],
            )
            if identity not in seen_values:
                seen_values.add(identity)
                unique.append(item)

        if len(unique) != 1:
            return None

        value_expression, value_source, value_offset = unique[0]
        return self.resolve(
            value_expression,
            value_source,
            value_offset,
            depth + 1,
            visited,
        )


class DbTypeIndex:
    def __init__(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            """
            SELECT
                node_id,
                node_type,
                qualified_name,
                display_name
            FROM node
            WHERE node_type IN (
                'JAVA_CLASS',
                'JAVA_INTERFACE',
                'JAVA_ENUM'
            )
              AND qualified_name IS NOT NULL
            """
        )

        self.by_qname: dict[str, DbType] = {}
        self.by_simple: dict[str, list[DbType]] = defaultdict(list)

        for row in rows:
            item = DbType(
                node_id=row[0],
                node_type=row[1],
                qualified_name=row[2],
                display_name=row[3],
            )
            self.by_qname[item.qualified_name] = item
            self.by_simple[item.display_name].append(item)

    def resolve_class(
        self,
        guessed_qname: str | None,
    ) -> DbType | None:
        if not guessed_qname:
            return None

        guessed_qname = guessed_qname.replace("$", ".")

        direct = self.by_qname.get(guessed_qname)
        if direct and direct.node_type == "JAVA_CLASS":
            return direct

        simple = guessed_qname.rsplit(".", 1)[-1]
        matches = [
            item
            for item in self.by_simple.get(simple, [])
            if item.node_type == "JAVA_CLASS"
        ]

        if len(matches) == 1:
            return matches[0]

        suffix_matches = [
            item
            for item in matches
            if item.qualified_name.endswith(
                "." + guessed_qname
            )
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        return None


def resolve_local_key(
    expression: str,
    source: JavaSource,
    offset: int,
) -> str | None:
    expression = normalize_expression(expression)
    match = re.match(
        r"^([A-Za-z_][A-Za-z0-9_.$]*)\s*\.class$",
        expression,
    )
    if not match:
        return None

    return source.resolve_type_name(
        match.group(1),
        offset,
    )


def build_fact(
    source: JavaSource,
    call: RegistrationCall,
    constants: ConstantResolver,
    db_types: DbTypeIndex,
) -> RegistrationFact:
    if call.service_kind == "binder":
        resolved_key = constants.resolve(
            call.key_expression,
            source,
            call.offset,
        )
    else:
        resolved_key = resolve_local_key(
            call.key_expression,
            source,
            call.offset,
        )

    guessed_type = source.resolve_instance_type(
        call.instance_expression,
        call.offset,
    )
    resolved_type = db_types.resolve_class(guessed_type)

    if resolved_key and resolved_type:
        status = "resolved"
    elif not resolved_key and not resolved_type:
        status = "unresolved_key_and_instance"
    elif not resolved_key:
        status = "unresolved_key"
    else:
        status = "unresolved_instance"

    identity = (
        f"{call.api}:"
        f"{source.source_path}:"
        f"{call.line}:"
        f"{call.offset}"
    )

    return RegistrationFact(
        registration_id=stable_id(
            "SERVICE_REGISTRATION",
            identity,
        ),
        api=call.api,
        service_kind=call.service_kind,
        key_expression=call.key_expression,
        resolved_key=resolved_key,
        instance_expression=call.instance_expression,
        resolved_instance_type=(
            resolved_type.qualified_name
            if resolved_type
            else None
        ),
        resolution_status=status,
        source_path=source.source_path,
        line=call.line,
        raw_call=call.raw_call,
        is_test_source=is_test_path(source.source_path),
    )


def import_fact(
    writer: GraphWriter,
    fact: RegistrationFact,
    db_types: DbTypeIndex,
) -> None:
    properties = {
        "api": fact.api,
        "service_kind": fact.service_kind,
        "key_expression": fact.key_expression,
        "resolved_key": fact.resolved_key,
        "instance_expression": fact.instance_expression,
        "resolved_instance_type": fact.resolved_instance_type,
        "resolution_status": fact.resolution_status,
        "raw_call": fact.raw_call,
        "is_test_source": fact.is_test_source,
    }

    writer.upsert_node(
        Node(
            node_id=fact.registration_id,
            node_type="SERVICE_REGISTRATION",
            display_name=(
                f"{fact.api}@{fact.line}"
            ),
            qualified_name=(
                f"{fact.api}:"
                f"{fact.source_path}:"
                f"{fact.line}"
            ),
            properties=properties,
            source_path=fact.source_path,
            line_start=fact.line,
            line_end=fact.line,
            extractor="service-registration-v0.1",
        )
    )

    file_id = stable_id("FILE", fact.source_path)
    writer.upsert_node(
        Node(
            node_id=file_id,
            node_type="FILE",
            display_name=Path(
                fact.source_path
            ).name,
            qualified_name=fact.source_path,
            source_path=fact.source_path,
            extractor="service-registration-v0.1",
        )
    )
    writer.upsert_edge(
        Edge(
            edge_type="DECLARED_IN",
            from_node_id=fact.registration_id,
            to_node_id=file_id,
            source_path=fact.source_path,
            line_start=fact.line,
            line_end=fact.line,
            extractor="service-registration-v0.1",
        )
    )

    if not fact.resolved_key:
        return

    if fact.service_kind == "binder":
        key_type = "BINDER_SERVICE_NAME"
        key_edge = "REGISTERS_BINDER_NAME"
        derived_edge = "REGISTERED_AS"
        display_name = fact.resolved_key
    else:
        key_type = "LOCAL_SERVICE_KEY"
        key_edge = "REGISTERS_LOCAL_KEY"
        derived_edge = "EXPOSED_AS_LOCAL_SERVICE"
        display_name = fact.resolved_key.rsplit(".", 1)[-1]

    key_id = stable_id(key_type, fact.resolved_key)
    writer.upsert_node(
        Node(
            node_id=key_id,
            node_type=key_type,
            display_name=display_name,
            qualified_name=fact.resolved_key,
            properties={
                "value": fact.resolved_key,
                "source_expression": fact.key_expression,
                "service_kind": fact.service_kind,
            },
            source_path=fact.source_path,
            line_start=fact.line,
            line_end=fact.line,
            extractor="service-registration-v0.1",
        )
    )
    writer.upsert_edge(
        Edge(
            edge_type=key_edge,
            from_node_id=fact.registration_id,
            to_node_id=key_id,
            source_path=fact.source_path,
            line_start=fact.line,
            line_end=fact.line,
            extractor="service-registration-v0.1",
        )
    )

    if not fact.resolved_instance_type:
        return

    instance = db_types.resolve_class(
        fact.resolved_instance_type
    )
    if not instance:
        return

    writer.upsert_edge(
        Edge(
            edge_type="REGISTERS_INSTANCE",
            from_node_id=fact.registration_id,
            to_node_id=instance.node_id,
            source_path=fact.source_path,
            line_start=fact.line,
            line_end=fact.line,
            extractor="service-registration-v0.1",
        )
    )

    if fact.is_test_source:
        return

    writer.upsert_edge(
        Edge(
            edge_type=derived_edge,
            from_node_id=instance.node_id,
            to_node_id=key_id,
            properties={
                "registration_id": fact.registration_id,
                "api": fact.api,
            },
            source_path=fact.source_path,
            line_start=fact.line,
            line_end=fact.line,
            extractor="service-registration-v0.1",
        )
    )


def scan_sources(
    frameworks_base: Path,
    source_root: Path,
) -> list[JavaSource]:
    sources: list[JavaSource] = []

    for path in frameworks_base.rglob("*.java"):
        try:
            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            continue

        if not any(
            token in text
            for token in (
                "ServiceManager.addService",
                "publishBinderService",
                "LocalServices.addService",
                "static final String",
                "public static final String",
            )
        ):
            continue

        sources.append(
            JavaSource(
                path,
                source_root,
                text,
            )
        )

    return sources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--frameworks-base",
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

    sources = scan_sources(
        args.frameworks_base,
        args.source_root,
    )
    constants = ConstantResolver(sources)

    with sqlite3.connect(args.db) as connection:
        db_types = DbTypeIndex(connection)

    facts: list[RegistrationFact] = []

    for source in sources:
        for call in find_registration_calls(source):
            facts.append(
                build_fact(
                    source,
                    call,
                    constants,
                    db_types,
                )
            )

    writer = GraphWriter(args.db)
    try:
        for fact in facts:
            import_fact(writer, fact, db_types)
    finally:
        writer.close()

    summary: dict[str, int] = defaultdict(int)
    for fact in facts:
        summary[fact.api] += 1
        summary[
            f"status:{fact.resolution_status}"
        ] += 1
        summary[
            f"kind:{fact.service_kind}"
        ] += 1
        if fact.is_test_source:
            summary["test_sources"] += 1

    report = {
        "summary": dict(sorted(summary.items())),
        "registrations": [
            {
                "registration_id": fact.registration_id,
                "api": fact.api,
                "service_kind": fact.service_kind,
                "key_expression": fact.key_expression,
                "resolved_key": fact.resolved_key,
                "instance_expression": fact.instance_expression,
                "resolved_instance_type": (
                    fact.resolved_instance_type
                ),
                "resolution_status": (
                    fact.resolution_status
                ),
                "source_path": fact.source_path,
                "line": fact.line,
                "raw_call": fact.raw_call,
                "is_test_source": fact.is_test_source,
            }
            for fact in facts
        ],
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

    resolved = sum(
        fact.resolution_status == "resolved"
        for fact in facts
    )
    print(
        f"Service registrations: {len(facts)}; "
        f"fully resolved: {resolved}; "
        f"unresolved: {len(facts) - resolved}; "
        f"test sources: "
        f"{sum(fact.is_test_source for fact in facts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
