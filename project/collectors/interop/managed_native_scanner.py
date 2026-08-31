from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from collectors.facts.model import (
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    SourceRange,
    SymbolFact,
)


SEMANTIC_PROFILE = "native-static-v0.1"
EXTRACTOR_VERSION = "0.1"


@dataclass(frozen=True)
class ParseResult:
    facts: tuple[Fact, ...]
    diagnostics: tuple[DiagnosticFact, ...]
    grammar_name: str
    grammar_fingerprint: str


@dataclass(frozen=True)
class _Token:
    value: str
    line: int
    column: int


def _tokens(text: str) -> tuple[_Token, ...]:
    result: list[_Token] = []
    offset = 0
    line = 1
    column = 1

    def advance() -> str:
        nonlocal offset, line, column
        value = text[offset]
        offset += 1
        if value == "\n":
            line += 1
            column = 1
        else:
            column += 1
        return value

    while offset < len(text):
        current = text[offset]
        if current.isspace():
            advance()
            continue
        if text.startswith("//", offset):
            while offset < len(text) and text[offset] != "\n":
                advance()
            continue
        if text.startswith("/*", offset):
            advance()
            advance()
            while offset < len(text) and not text.startswith("*/", offset):
                advance()
            if offset < len(text):
                advance()
                advance()
            continue
        if current in {'"', "'"}:
            quote = advance()
            while offset < len(text):
                value = advance()
                if value == "\\" and offset < len(text):
                    advance()
                elif value == quote:
                    break
            continue
        start_line, start_column = line, column
        if current.isalpha() or current in "_$":
            value = [advance()]
            while offset < len(text) and (
                text[offset].isalnum() or text[offset] in "_$"
            ):
                value.append(advance())
            result.append(_Token("".join(value), start_line, start_column))
            continue
        if text.startswith("...", offset):
            advance()
            advance()
            advance()
            result.append(_Token("...", start_line, start_column))
            continue
        result.append(_Token(advance(), start_line, start_column))
    return tuple(result)


def _package(tokens: tuple[_Token, ...]) -> str:
    for index, token in enumerate(tokens):
        if token.value != "package":
            continue
        parts: list[str] = []
        for item in tokens[index + 1:]:
            if item.value == ";" or item.value in {"class", "interface", "object"}:
                break
            if item.value != ".":
                parts.append(item.value)
        return ".".join(parts)
    return ""


def _split(values: list[str], separator: str = ",") -> list[list[str]]:
    parts: list[list[str]] = []
    current: list[str] = []
    depth = 0
    pairs = {"(": ")", "[": "]", "<": ">"}
    closing = set(pairs.values())
    for value in values:
        if value in pairs:
            depth += 1
        elif value in closing:
            depth -= 1
        if value == separator and depth == 0:
            if current:
                parts.append(current)
            current = []
        else:
            current.append(value)
    if current:
        parts.append(current)
    return parts


_PRIMITIVES = {
    "void": "V",
    "boolean": "Z",
    "byte": "B",
    "char": "C",
    "short": "S",
    "int": "I",
    "long": "J",
    "float": "F",
    "double": "D",
    "Unit": "V",
    "Boolean": "Z",
    "Byte": "B",
    "Char": "C",
    "Short": "S",
    "Int": "I",
    "Long": "J",
    "Float": "F",
    "Double": "D",
}
_JAVA_LANG = {
    "String",
    "Object",
    "Class",
    "Throwable",
    "CharSequence",
}
_KOTLIN_ARRAYS = {
    "BooleanArray": "[Z",
    "ByteArray": "[B",
    "CharArray": "[C",
    "ShortArray": "[S",
    "IntArray": "[I",
    "LongArray": "[J",
    "FloatArray": "[F",
    "DoubleArray": "[D",
}


def _descriptor(values: list[str], package_name: str, language: str) -> str:
    tokens = [item for item in values if item not in {"?", "final", "vararg"}]
    array_depth = tokens.count("[") + tokens.count("...")
    tokens = [item for item in tokens if item not in {"[", "]", "..."}]
    if language == "kotlin" and tokens and tokens[0] in _KOTLIN_ARRAYS:
        return _KOTLIN_ARRAYS[tokens[0]]
    if language == "kotlin" and tokens[:2] == ["Array", "<"] and ">" in tokens:
        close = len(tokens) - 1 - tokens[::-1].index(">")
        return "[" + _descriptor(tokens[2:close], package_name, language)
    generic_depth = 0
    erased: list[str] = []
    for token in tokens:
        if token == "<":
            generic_depth += 1
        elif token == ">":
            generic_depth -= 1
        elif generic_depth == 0:
            erased.append(token)
    name = "".join(erased)
    if name in _PRIMITIVES:
        base = _PRIMITIVES[name]
    else:
        if name in _JAVA_LANG or (language == "kotlin" and name == "String"):
            qualified = f"java.lang.{name}"
        elif "." in name:
            qualified = name
        elif package_name:
            qualified = f"{package_name}.{name}"
        else:
            qualified = name
        base = f"L{qualified.replace('.', '/')};"
    if base == "V" and array_depth:
        raise ValueError("void cannot be an array element")
    return "[" * array_depth + base


def _parameter_descriptors(
    values: list[str], package_name: str, language: str
) -> str:
    descriptors: list[str] = []
    for parameter in _split(values):
        if language == "java":
            identifiers = [
                index
                for index, value in enumerate(parameter)
                if value and (value[0].isalpha() or value[0] in "_$")
            ]
            if not identifiers:
                continue
            variable_index = identifiers[-1]
            type_tokens = parameter[:variable_index]
        else:
            if ":" not in parameter:
                continue
            colon = parameter.index(":")
            type_tokens = parameter[colon + 1:]
            if "=" in type_tokens:
                type_tokens = type_tokens[:type_tokens.index("=")]
        descriptors.append(_descriptor(type_tokens, package_name, language))
    return "".join(descriptors)


def _matching(tokens: tuple[_Token, ...], start: int, opening: str, closing: str) -> int:
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index].value == opening:
            depth += 1
        elif tokens[index].value == closing:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unclosed {opening}")


def _workspace_path(path: Path, repository: str) -> str:
    raw = path.as_posix()
    marker = f"/{repository.strip('/')}/"
    searchable = f"/{raw.lstrip('/')}"
    if marker in searchable:
        return repository.strip("/") + "/" + searchable.split(marker, 1)[1]
    return PurePosixPath(raw).as_posix()


def scan_managed_native(
    path: Path,
    language: str,
    repository: str,
    revision: str | None,
) -> ParseResult:
    if language not in {"java", "kotlin"}:
        raise ValueError("managed native scanner supports java or kotlin")
    source = path.read_bytes()
    text = source.decode("utf-8")
    tokens = _tokens(text)
    package_name = _package(tokens)
    source_path = _workspace_path(path, repository)
    grammar_name = f"managed-native-structural-{language}"
    grammar_payload = json.dumps(
        {"name": grammar_name, "version": EXTRACTOR_VERSION},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    grammar_fingerprint = hashlib.sha256(grammar_payload).hexdigest()
    evidence = Evidence(
        repository=repository,
        extractor=grammar_name,
        extractor_version=EXTRACTOR_VERSION,
        evidence_kind=EvidenceKind.SOURCE_DECLARATION,
        source_revision=revision,
        content_fingerprint=hashlib.sha256(source).hexdigest(),
        platform_identity="unknown",
        semantic_profile_version=SEMANTIC_PROFILE,
    )
    facts: list[Fact] = []
    diagnostics: list[DiagnosticFact] = []
    class_stack: list[tuple[str, int]] = []
    pending_class: str | None = None
    brace_depth = 0
    index = 0

    def class_name() -> str:
        local = "$".join(item[0] for item in class_stack)
        return f"{package_name}.{local}" if package_name else local

    def emit(
        token: _Token,
        method: str,
        descriptor: str,
        is_static: bool,
    ) -> None:
        owner = class_name()
        identity = f"managed-native:{language}:{owner}#{method}{descriptor}"
        facts.append(
            SymbolFact(
                language=language,
                fact_kind="MANAGED_NATIVE_DECLARATION",
                logical_identity=identity,
                source_range=SourceRange(
                    source_path=source_path,
                    line_start=token.line,
                    line_end=token.line,
                    column_start=token.column,
                    column_end=token.column + len(token.value),
                ),
                evidence=evidence,
                properties={
                    "class_name": owner,
                    "method_name": method,
                    "descriptor": descriptor,
                    "static": is_static,
                },
            )
        )

    try:
        while index < len(tokens):
            value = tokens[index].value
            if value in {"class", "interface", "object"} and index + 1 < len(tokens):
                pending_class = tokens[index + 1].value
                index += 2
                continue
            if value == "{":
                brace_depth += 1
                if pending_class:
                    class_stack.append((pending_class, brace_depth))
                    pending_class = None
                index += 1
                continue
            if value == "}":
                if class_stack and class_stack[-1][1] == brace_depth:
                    class_stack.pop()
                brace_depth -= 1
                index += 1
                continue
            if language == "java" and value == "native" and class_stack:
                open_paren = next(
                    position
                    for position in range(index + 1, len(tokens))
                    if tokens[position].value == "("
                )
                close_paren = _matching(tokens, open_paren, "(", ")")
                declaration = [item.value for item in tokens[index + 1:open_paren]]
                method = declaration[-1]
                return_type = declaration[:-1]
                arguments = [item.value for item in tokens[open_paren + 1:close_paren]]
                descriptor = (
                    "(" + _parameter_descriptors(arguments, package_name, language) + ")"
                    + _descriptor(return_type, package_name, language)
                )
                boundary = index - 1
                modifiers: set[str] = set()
                while boundary >= 0 and tokens[boundary].value not in {";", "{", "}"}:
                    modifiers.add(tokens[boundary].value)
                    boundary -= 1
                emit(tokens[index], method, descriptor, "static" in modifiers)
                index = close_paren + 1
                continue
            if (
                language == "kotlin"
                and value == "external"
                and index + 2 < len(tokens)
                and tokens[index + 1].value == "fun"
                and class_stack
            ):
                method_token = tokens[index + 2]
                open_paren = index + 3
                if tokens[open_paren].value != "(":
                    raise ValueError("Kotlin external function is missing parameters")
                close_paren = _matching(tokens, open_paren, "(", ")")
                arguments = [item.value for item in tokens[open_paren + 1:close_paren]]
                return_tokens = ["Unit"]
                if close_paren + 1 < len(tokens) and tokens[close_paren + 1].value == ":":
                    end = close_paren + 2
                    while end < len(tokens) and tokens[end].value not in {
                        "external", "fun", "class", "}", "{", ";"
                    }:
                        end += 1
                    return_tokens = [item.value for item in tokens[close_paren + 2:end]]
                descriptor = (
                    "(" + _parameter_descriptors(arguments, package_name, language) + ")"
                    + _descriptor(return_tokens, package_name, language)
                )
                emit(tokens[index], method_token.value, descriptor, False)
                index = close_paren + 1
                continue
            index += 1
    except (StopIteration, ValueError) as error:
        diagnostics.append(
            DiagnosticFact(
                language=language,
                fact_kind="DIAGNOSTIC",
                logical_identity=f"diagnostic:managed_native_parse:{source_path}",
                category="parse_error",
                reason_code="managed_native_parse_error",
                message=str(error),
                source_range=SourceRange(source_path=source_path, line_start=1, line_end=1),
                evidence=Evidence(
                    **{**evidence.__dict__, "evidence_kind": EvidenceKind.UNRESOLVED_REFERENCE}
                ),
                properties={},
            )
        )
        facts.clear()
    return ParseResult(
        facts=tuple(sorted(facts, key=lambda item: item.logical_identity)),
        diagnostics=tuple(diagnostics),
        grammar_name=grammar_name,
        grammar_fingerprint=grammar_fingerprint,
    )
