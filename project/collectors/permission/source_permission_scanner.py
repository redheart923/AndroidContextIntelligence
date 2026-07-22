from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from collectors.permission.lexical import (
    LexicalError,
    SourceConstruct,
    iter_permission_constructs,
    mask_non_code,
)
from collectors.permission.model import (
    PARSER_VERSION,
    ParseOutcome,
    PermissionDiagnostic,
    PermissionEvidence,
    PermissionFact,
    PermissionFactKind,
)
from collectors.permission.resolver import (
    Resolution,
    SourceBindings,
    resolve_permission_expression,
)


EXTRACTOR_NAME = "source_permission_scanner"
ENFORCEMENT_APIS = {
    "enforceCallingPermission": "single",
    "enforceCallingOrSelfPermission": "single",
    "enforcePermission": "single",
    "enforceAnyPermissionOf": "any_of",
    "enforceAllPermissions": "all_of",
}
CHECK_APIS = frozenset(
    {
        "checkCallingPermission",
        "checkCallingOrSelfPermission",
        "checkPermission",
        "checkSelfPermission",
    }
)


@dataclass(frozen=True)
class MethodRange:
    node_id: str
    line_start: int
    line_end: int | None


def load_method_ranges(
    connection: sqlite3.Connection,
    source_path: str,
) -> tuple[MethodRange, ...]:
    rows = connection.execute(
        """
        SELECT node_id, line_start, line_end
        FROM node
        WHERE source_path = ?
          AND node_type IN ('JAVA_METHOD', 'KOTLIN_METHOD')
          AND line_start IS NOT NULL AND line_end IS NOT NULL
        ORDER BY line_start, line_end, node_id
        """,
        (source_path,),
    )
    return tuple(MethodRange(row[0], row[1], row[2]) for row in rows)


def find_containing_method(
    methods: Iterable[MethodRange],
    line_start: int,
    line_end: int,
) -> MethodRange | None:
    candidates = [
        method
        for method in methods
        if method.line_end is not None
        and method.line_start <= line_start
        and line_end <= method.line_end
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (item.line_end - item.line_start, item.node_id),
    )


def _split_top_level(expression: str) -> tuple[str, ...]:
    masked = mask_non_code(expression)
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    start = 0
    parts: list[str] = []
    for index, character in enumerate(masked):
        if character in pairs:
            stack.append(pairs[character])
        elif character in ")]}":
            if stack and stack[-1] == character:
                stack.pop()
        elif character == "," and not stack:
            parts.append(expression[start:index].strip())
            start = index + 1
    final = expression[start:].strip()
    if final:
        parts.append(final)
    return tuple(parts)


def _named_argument(part: str) -> tuple[str | None, str]:
    masked = mask_non_code(part)
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    for index, character in enumerate(masked):
        if character in pairs:
            stack.append(pairs[character])
        elif character in ")]}":
            if stack and stack[-1] == character:
                stack.pop()
        elif character == "=" and not stack:
            name = part[:index].strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                return name, part[index + 1:].strip()
    return None, part.strip()


def _diagnostic(
    *,
    category: str,
    reason_code: str,
    repository: str,
    source_path: str,
    line_start: int | None,
    line_end: int | None,
    expression: str | None,
    message: str,
    language: str,
) -> PermissionDiagnostic:
    return PermissionDiagnostic(
        category=category,
        reason_code=reason_code,
        repository=repository,
        source_path=source_path,
        line_start=line_start,
        line_end=line_end,
        expression=expression,
        message=message,
        properties={"language": language},
    )


def _evidence(
    construct: SourceConstruct,
    *,
    repository: str,
    source_path: str,
    source_revision: str,
    language: str,
    expression: str,
) -> PermissionEvidence:
    return PermissionEvidence(
        repository=repository,
        source_path=source_path,
        source_dialect=language,
        source_expression=expression,
        line_start=construct.line_start,
        line_end=construct.line_end,
        source_revision=source_revision,
        parser=EXTRACTOR_NAME,
        parser_version=PARSER_VERSION,
    )


def _line_offsets(text: str) -> tuple[int, ...]:
    offsets = [0]
    offsets.extend(match.end() for match in re.finditer("\n", text))
    return tuple(offsets)


def _annotation_owner(
    construct: SourceConstruct,
    methods: tuple[MethodRange, ...],
    masked: str,
    offsets: tuple[int, ...],
) -> MethodRange | None:
    candidates = sorted(
        (
            method
            for method in methods
            if method.line_end is not None
            and method.line_start >= construct.line_start
        ),
        key=lambda item: (item.line_start, item.line_end or item.line_start, item.node_id),
    )
    if not candidates:
        return None
    method = candidates[0]
    if method.line_start == construct.line_start:
        return method
    if method.line_start - 1 >= len(offsets):
        return None
    between = masked[construct.offset_end:offsets[method.line_start - 1]]
    if re.search(r"[;{}=]", between):
        return None
    if re.search(r"\b(?:class|interface|object|enum)\b", between):
        return None
    return method


def _is_method_declaration(
    construct: SourceConstruct,
    owner: MethodRange,
    masked: str,
    offsets: tuple[int, ...],
) -> bool:
    if construct.kind != "call" or owner.line_start != construct.line_start:
        return False
    if construct.line_start - 1 >= len(offsets):
        return False
    prefix = masked[offsets[construct.line_start - 1]:construct.offset_start]
    if "{" in prefix:
        return False
    return f"#{construct.name}" in owner.node_id


def _facts_for_resolution(
    resolution: Resolution,
    *,
    kind: PermissionFactKind,
    owner: MethodRange,
    construct: SourceConstruct,
    expression: str,
    mode: str,
    properties: dict[str, object],
    repository: str,
    source_path: str,
    source_revision: str,
    language: str,
) -> tuple[PermissionFact, ...]:
    shared_properties = {
        **properties,
        "api_name": construct.name,
        "argument_expression": expression,
        "resolution_kind": resolution.kind,
        "requirement_mode": mode,
    }
    return tuple(
        PermissionFact(
            kind=kind,
            permission_name=value,
            package_name=None,
            owner_node_id=owner.node_id,
            properties=shared_properties,
            evidence=_evidence(
                construct,
                repository=repository,
                source_path=source_path,
                source_revision=source_revision,
                language=language,
                expression=expression,
            ),
        )
        for value in resolution.values
    )


def _annotation_expression(
    construct: SourceConstruct,
) -> tuple[str, str, dict[str, object]]:
    positional: list[str] = []
    named: dict[str, str] = {}
    for part in _split_top_level(construct.argument_expression):
        name, value = _named_argument(part)
        if name is None:
            positional.append(value)
        else:
            named[name] = value
    if "allOf" in named:
        form, mode, expression = "allOf", "all_of", named["allOf"]
    elif "anyOf" in named:
        form, mode, expression = "anyOf", "any_of", named["anyOf"]
    elif "value" in named:
        form, mode, expression = "value", "single", named["value"]
    else:
        form, mode = "value", "single"
        expression = positional[0] if positional else ""
    conditional = named.get("conditional", "false").strip().lower() == "true"
    return expression, mode, {
        "annotation_form": form,
        "conditional": conditional,
    }


def scan_permission_source(
    path: Path,
    *,
    repository: str,
    source_path: str,
    source_revision: str,
    language: str,
    methods: tuple[MethodRange, ...],
) -> ParseOutcome:
    text = path.read_text(encoding="utf-8", errors="replace")
    masked = mask_non_code(text)
    bindings = SourceBindings.from_text(text, language)
    facts: list[PermissionFact] = []
    diagnostics: list[PermissionDiagnostic] = []

    for match in re.finditer(r"@\s*RequiresPermission\.(?:Read|Write)\b", masked):
        line = masked.count("\n", 0, match.start()) + 1
        diagnostics.append(
            _diagnostic(
                category="unsupported_constructs",
                reason_code="nested_requires_permission",
                repository=repository,
                source_path=source_path,
                line_start=line,
                line_end=line,
                expression=match.group(0),
                message="nested RequiresPermission.Read/Write is not supported",
                language=language,
            )
        )

    try:
        constructs = iter_permission_constructs(text)
    except LexicalError as error:
        return ParseOutcome(
            diagnostics=tuple(diagnostics) + (
                _diagnostic(
                    category="unsupported_constructs",
                    reason_code="lexical_error",
                    repository=repository,
                    source_path=source_path,
                    line_start=error.line_start,
                    line_end=error.line_start,
                    expression=None,
                    message=str(error),
                    language=language,
                ),
            ),
            counters={f"files_scanned.{language}": 1},
        )

    offsets = _line_offsets(text)
    for construct in constructs:
        extra_properties: dict[str, object] = {}
        if construct.kind == "annotation":
            owner = _annotation_owner(construct, methods, masked, offsets)
            expression, mode, extra_properties = _annotation_expression(construct)
            kind = PermissionFactKind.REQUIRES_PERMISSION
        else:
            owner = find_containing_method(
                methods,
                construct.line_start,
                construct.line_end,
            )
            arguments = _split_top_level(construct.argument_expression)
            if construct.name in CHECK_APIS:
                kind = PermissionFactKind.CHECKS_PERMISSION
                mode = "single"
            else:
                kind = PermissionFactKind.ENFORCES_PERMISSION
                mode = ENFORCEMENT_APIS[construct.name]
            if mode == "single":
                expression = arguments[0] if arguments else ""
            elif len(arguments) == 1:
                expression = arguments[0]
            else:
                expression = "{" + ", ".join(arguments) + "}"

        if owner is None:
            diagnostics.append(
                _diagnostic(
                    category="unresolved_method_owners",
                    reason_code="no_bounded_method_owner",
                    repository=repository,
                    source_path=source_path,
                    line_start=construct.line_start,
                    line_end=construct.line_end,
                    expression=construct.argument_expression,
                    message="construct is not contained by a unique bounded method",
                    language=language,
                )
            )
            continue

        if _is_method_declaration(construct, owner, masked, offsets):
            continue

        resolution = resolve_permission_expression(expression, bindings)
        if resolution.reason_code:
            diagnostics.append(
                _diagnostic(
                    category="unresolved_permission_expressions",
                    reason_code=resolution.reason_code,
                    repository=repository,
                    source_path=source_path,
                    line_start=construct.line_start,
                    line_end=construct.line_end,
                    expression=expression,
                    message="permission expression could not be resolved",
                    language=language,
                )
            )
            continue
        facts.extend(
            _facts_for_resolution(
                resolution,
                kind=kind,
                owner=owner,
                construct=construct,
                expression=expression,
                mode=mode,
                properties=extra_properties,
                repository=repository,
                source_path=source_path,
                source_revision=source_revision,
                language=language,
            )
        )

    return ParseOutcome(
        facts=tuple(facts),
        diagnostics=tuple(diagnostics),
        counters={f"files_scanned.{language}": 1},
    )
