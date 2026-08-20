from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .model import DefinitionRecord


@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    logical_method_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"unique", "ambiguous", "unmatched", "synthetic"}:
            raise ValueError(f"invalid reconciliation status: {self.status!r}")
        if self.status == "unique" and len(self.logical_method_ids) != 1:
            raise ValueError("unique reconciliation must have one logical method")
        if self.status in {"unmatched", "synthetic"} and self.logical_method_ids:
            raise ValueError(f"{self.status} reconciliation must not have logical methods")


@dataclass(frozen=True)
class _Candidate:
    node_id: str
    owner: str
    name: str
    parameters: tuple[str, ...]
    source_path: str
    line_start: int | None
    line_end: int | None


ANNOTATION = re.compile(r"@[\w.$]+(?:\([^)]*\))?\s*")
MODIFIERS = re.compile(r"\b(?:final|volatile|transient|crossinline|noinline|vararg)\b\s*")
GENERIC = re.compile(r"<[^<>]*>")


def _split_parameters(signature: str) -> tuple[str, ...]:
    value = signature.strip()
    if not value:
        return ()
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character in "<([":
            depth += 1
        elif character in ">)]" and depth:
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return tuple(_normalize_parameter(part) for part in parts if part.strip())


def _erase_generics(value: str) -> str:
    previous = ""
    while previous != value:
        previous = value
        value = GENERIC.sub("", value)
    return value


def _normalize_parameter(value: str) -> str:
    result = ANNOTATION.sub("", value).strip()
    result = MODIFIERS.sub("", result).strip()
    if ":" in result and not result.startswith("?"):
        # Kotlin ctags commonly emits `name: Type`.
        result = result.split(":", 1)[1].strip()
    result = _erase_generics(result)
    result = result.replace("...", "[]").replace("$", ".")
    result = re.sub(r"\s+", " ", result).strip()
    tokens = result.split(" ")
    if len(tokens) > 1 and re.fullmatch(r"[A-Za-z_$][\w$]*(?:\[\])?", tokens[-1]):
        suffix = "[]" if tokens[-1].endswith("[]") else ""
        result = " ".join(tokens[:-1]) + suffix
    return result.replace(" ", "")


def _simple_type(value: str) -> str:
    array = ""
    while value.endswith("[]"):
        array += "[]"
        value = value[:-2]
    return value.rsplit(".", 1)[-1] + array


def _compatible_parameters(
    actual: tuple[str, ...], expected: tuple[str, ...]
) -> bool:
    if len(actual) != len(expected):
        return False
    return all(
        left == right or _simple_type(left) == _simple_type(right)
        for left, right in zip(actual, expected, strict=True)
    )


def _parse_qualified_name(value: str) -> tuple[str, str, tuple[str, ...]] | None:
    if "#" not in value:
        return None
    owner, member = value.split("#", 1)
    match = re.fullmatch(r"([^()]*)\((.*)\)", member)
    if match is None:
        return None
    return (
        owner.replace("$", "."),
        match.group(1),
        _split_parameters(match.group(2)),
    )


def _is_synthetic(definition: DefinitionRecord) -> bool:
    declaring = definition.declaring_type.lower()
    name = definition.callable_name.lower()
    return (
        "<anonymous>@" in declaring
        or "<lambda>" in declaring
        or name.startswith("lambda$")
        or "$lambda" in name
        or name.startswith("access$")
    )


def _load_candidates(database: Path, language: str) -> tuple[_Candidate, ...]:
    node_type = "JAVA_METHOD" if language == "java" else "KOTLIN_METHOD"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT node_id, qualified_name, COALESCE(source_path, ''),
                   line_start, line_end
            FROM node
            WHERE node_type = ? AND qualified_name IS NOT NULL
            ORDER BY node_id
            """,
            (node_type,),
        ).fetchall()
    result: list[_Candidate] = []
    for node_id, qualified_name, source_path, line_start, line_end in rows:
        parsed = _parse_qualified_name(str(qualified_name))
        if parsed is None:
            continue
        owner, name, parameters = parsed
        result.append(
            _Candidate(
                node_id=str(node_id), owner=owner, name=name, parameters=parameters,
                source_path=str(source_path).replace("\\", "/"),
                line_start=int(line_start) if line_start is not None else None,
                line_end=int(line_end) if line_end is not None else None,
            )
        )
    return tuple(result)


def reconcile_definition(
    database: Path,
    definition: DefinitionRecord,
) -> ReconciliationResult:
    if _is_synthetic(definition):
        return ReconciliationResult(
            "synthetic", (), ("compiler_or_anonymous_callable",)
        )
    expected_owner = definition.declaring_type.replace("$", ".")
    compatible = [
        candidate
        for candidate in _load_candidates(database, definition.language)
        if candidate.owner == expected_owner
        and candidate.name == definition.callable_name
        and _compatible_parameters(candidate.parameters, definition.erased_parameters)
    ]
    if not compatible:
        return ReconciliationResult(
            "unmatched",
            (),
            ("no_owner_name_erased_parameter_match", definition.symbol_key),
        )
    narrowed = compatible
    expected_path = definition.span.source_path.replace("\\", "/")
    path_matches = [item for item in narrowed if item.source_path == expected_path]
    if path_matches:
        narrowed = path_matches
    line_matches = [
        item
        for item in narrowed
        if item.line_start is not None
        and item.line_end is not None
        and item.line_start <= definition.span.start_line <= item.line_end
    ]
    if line_matches:
        narrowed = line_matches
    ids = tuple(sorted(item.node_id for item in narrowed))
    if len(ids) == 1:
        diagnostics = (
            "owner_name_erased_parameters",
            "source_path" if path_matches else "source_path_unavailable",
            "source_line" if line_matches else "source_line_unavailable",
        )
        return ReconciliationResult("unique", ids, diagnostics)
    return ReconciliationResult(
        "ambiguous",
        ids,
        ("multiple_compatible_logical_methods", definition.symbol_key),
    )
