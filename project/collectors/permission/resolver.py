from __future__ import annotations

import json
import re
from dataclasses import dataclass

from collectors.permission.lexical import mask_comments, mask_non_code


MAX_ALIAS_DEPTH = 16
IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
MANIFEST_CONSTANT = re.compile(
    r"^(?:(?P<qualified>android)\.)?Manifest\.permission\.(?P<name>[A-Z0-9_]+)$"
)


@dataclass(frozen=True)
class Resolution:
    values: tuple[str, ...]
    kind: str
    reason_code: str | None = None


@dataclass(frozen=True)
class SourceBindings:
    language: str
    package_name: str | None
    imports: dict[str, tuple[str, ...]]
    static_imports: dict[str, tuple[str, ...]]
    static_wildcards: tuple[str, ...]
    constants: dict[str, tuple[str, ...]]

    @classmethod
    def from_text(cls, text: str, language: str) -> "SourceBindings":
        normalized_language = language.lower()
        if normalized_language not in {"java", "kotlin"}:
            raise ValueError(f"unsupported source language: {language}")
        code = _strip_comments(text)
        package_match = re.search(
            r"^\s*package\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;?",
            code,
            re.MULTILINE,
        )
        imports: dict[str, list[str]] = {}
        static_imports: dict[str, list[str]] = {}
        static_wildcards: list[str] = []

        for match in re.finditer(
            r"^\s*import\s+(?P<static>static\s+)?"
            r"(?P<path>[A-Za-z_][A-Za-z0-9_.*$]*)"
            r"(?:\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?\s*;?",
            code,
            re.MULTILINE,
        ):
            path = match.group("path")
            alias = match.group("alias")
            is_permission_constant = ".permission." in path
            is_static = bool(match.group("static")) or (
                normalized_language == "kotlin" and is_permission_constant
            )
            if path.endswith(".*") and is_static:
                static_wildcards.append(path[:-2])
                continue
            simple_name = alias or path.rsplit(".", 1)[-1]
            target = static_imports if is_static else imports
            target.setdefault(simple_name, []).append(path)

        constants: dict[str, list[str]] = {}
        if normalized_language == "java":
            pattern = re.compile(
                r"\b(?:static\s+final|final\s+static)\s+"
                r"String(?:\s*\[\s*\])?\s+"
                r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
                r"(?P<value>.*?);",
                re.DOTALL,
            )
        else:
            pattern = re.compile(
                r"\bconst\s+val\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
                r"(?:\s*:\s*String)?\s*=\s*(?P<value>[^\n;]+)"
            )
        for match in pattern.finditer(code):
            constants.setdefault(match.group("name"), []).append(
                match.group("value").strip()
            )

        return cls(
            language=normalized_language,
            package_name=package_match.group(1) if package_match else None,
            imports={key: tuple(sorted(set(value))) for key, value in imports.items()},
            static_imports={
                key: tuple(sorted(set(value)))
                for key, value in static_imports.items()
            },
            static_wildcards=tuple(sorted(set(static_wildcards))),
            constants={
                key: tuple(sorted(set(value)))
                for key, value in constants.items()
            },
        )


def _strip_comments(text: str) -> str:
    return mask_comments(text)


def _split_top_level(expression: str) -> tuple[str, ...]:
    masked = mask_non_code(expression)
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
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


def _collection_contents(expression: str) -> str | None:
    value = expression.strip()
    java_array = re.fullmatch(
        r"new\s+String\s*\[\s*\]\s*\{(?P<body>.*)\}",
        value,
        re.DOTALL,
    )
    if java_array:
        return java_array.group("body")
    direct_array = re.fullmatch(r"\{(?P<body>.*)\}", value, re.DOTALL)
    if direct_array:
        return direct_array.group("body")
    kotlin_array = re.fullmatch(r"\[(?P<body>.*)\]", value, re.DOTALL)
    if kotlin_array:
        return kotlin_array.group("body")
    function = re.fullmatch(
        r"(?:arrayOf|listOf|setOf|Arrays\.asList)\s*\((?P<body>.*)\)",
        value,
        re.DOTALL,
    )
    return function.group("body") if function else None


def _literal(expression: str) -> str | None:
    if not re.fullmatch(r'"(?:\\.|[^"\\])*"', expression, re.DOTALL):
        return None
    try:
        value = json.loads(expression)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, str) else None


def resolve_permission_expression(
    expression: str,
    bindings: SourceBindings,
) -> Resolution:
    return _resolve(expression.strip(), bindings, frozenset(), 0)


def _resolve(
    expression: str,
    bindings: SourceBindings,
    visited: frozenset[str],
    depth: int,
) -> Resolution:
    if depth > MAX_ALIAS_DEPTH:
        return Resolution((), "unresolved", "alias_depth")

    literal = _literal(expression)
    if literal is not None:
        return Resolution((literal,), "literal")

    manifest = MANIFEST_CONSTANT.fullmatch(expression)
    if manifest:
        if not manifest.group("qualified"):
            candidates = bindings.imports.get("Manifest", ())
            if len(candidates) > 1:
                return Resolution((), "unresolved", "ambiguous_import")
            if candidates != ("android.Manifest",):
                return Resolution((), "unresolved", "unsupported_expression")
        return Resolution(
            (f"android.permission.{manifest.group('name')}",),
            "manifest_constant",
        )

    collection = _collection_contents(expression)
    if collection is not None:
        values: list[str] = []
        for part in _split_top_level(collection):
            resolved = _resolve(part, bindings, visited, depth + 1)
            if resolved.reason_code:
                return resolved
            for value in resolved.values:
                if value not in values:
                    values.append(value)
        return Resolution(tuple(values), "collection")

    if not IDENTIFIER.fullmatch(expression):
        return Resolution((), "unresolved", "unsupported_expression")

    imported = bindings.static_imports.get(expression, ())
    wildcard_matches = tuple(
        f"{wildcard}.{expression}"
        for wildcard in bindings.static_wildcards
        if wildcard == "android.Manifest.permission"
    )
    import_candidates = tuple(sorted(set(imported + wildcard_matches)))
    if len(import_candidates) > 1:
        return Resolution((), "unresolved", "ambiguous_import")
    if import_candidates:
        target = import_candidates[0]
        match = re.fullmatch(
            r"android\.Manifest\.permission\.([A-Z0-9_]+)",
            target,
        )
        if match:
            return Resolution(
                (f"android.permission.{match.group(1)}",),
                "static_import",
            )

    aliases = bindings.constants.get(expression, ())
    if len(aliases) > 1:
        return Resolution((), "unresolved", "ambiguous_binding")
    if aliases:
        if expression in visited:
            return Resolution((), "unresolved", "alias_cycle")
        resolved = _resolve(
            aliases[0],
            bindings,
            visited | {expression},
            depth + 1,
        )
        if resolved.reason_code:
            return resolved
        return Resolution(resolved.values, "alias")

    return Resolution((), "unresolved", "unsupported_expression")
