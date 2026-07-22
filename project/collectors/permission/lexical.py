from __future__ import annotations

import re
from dataclasses import dataclass


ANNOTATIONS = frozenset({"RequiresPermission"})
CALLS = frozenset(
    {
        "enforceCallingPermission",
        "enforceCallingOrSelfPermission",
        "enforcePermission",
        "enforceAnyPermissionOf",
        "enforceAllPermissions",
        "checkCallingPermission",
        "checkCallingOrSelfPermission",
        "checkPermission",
        "checkSelfPermission",
    }
)


class LexicalError(ValueError):
    def __init__(self, message: str, line_start: int) -> None:
        super().__init__(message)
        self.line_start = line_start


@dataclass(frozen=True)
class SourceConstruct:
    kind: str
    name: str
    argument_expression: str
    line_start: int
    line_end: int
    offset_start: int
    offset_end: int


def _mask_source(text: str, *, mask_literals: bool) -> str:
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
                if mask_literals:
                    for position in range(index, index + 3):
                        mask(position)
                index += 3; state = "triple_string"
                continue
            if text[index] == '"':
                if mask_literals:
                    mask(index)
                index += 1; state = "string"
                continue
            if text[index] == "'":
                if mask_literals:
                    mask(index)
                index += 1; state = "character"
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
                if mask_literals:
                    for position in range(index, index + 3):
                        mask(position)
                index += 3; state = "code"
                continue
            if mask_literals:
                mask(index)
            index += 1
            continue

        if text[index] == "\\" and index + 1 < len(text):
            if mask_literals:
                mask(index); mask(index + 1)
            index += 2
            continue
        delimiter = '"' if state == "string" else "'"
        if text[index] == delimiter:
            if mask_literals:
                mask(index)
            index += 1; state = "code"
            continue
        if mask_literals:
            mask(index)
        index += 1

    return "".join(result)


def mask_non_code(text: str) -> str:
    """Replace comments and literals with spaces without changing layout."""
    return _mask_source(text, mask_literals=True)


def mask_comments(text: str) -> str:
    """Replace comments while retaining literals and exact source layout."""
    return _mask_source(text, mask_literals=False)


def _closing_delimiter(masked: str, opening: int, line_start: int) -> int:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    for index in range(opening, len(masked)):
        character = masked[index]
        if character in pairs:
            stack.append(pairs[character])
        elif character in ")]}":
            if not stack or character != stack.pop():
                raise LexicalError("mismatched delimiter", line_start)
            if not stack:
                return index
    raise LexicalError("unterminated permission construct", line_start)


def iter_permission_constructs(text: str) -> tuple[SourceConstruct, ...]:
    masked = mask_non_code(text)
    annotation_names = "|".join(sorted(ANNOTATIONS))
    call_names = "|".join(sorted(CALLS))
    patterns = (
        ("annotation", re.compile(rf"@\s*(?P<name>{annotation_names})\s*\(")),
        ("call", re.compile(rf"\b(?P<name>{call_names})\s*\(")),
    )
    found: list[SourceConstruct] = []
    for kind, pattern in patterns:
        for match in pattern.finditer(masked):
            opening = masked.find("(", match.start(), match.end())
            line_start = masked.count("\n", 0, match.start()) + 1
            closing = _closing_delimiter(masked, opening, line_start)
            line_end = masked.count("\n", 0, closing) + 1
            found.append(
                SourceConstruct(
                    kind=kind,
                    name=match.group("name"),
                    argument_expression=text[opening + 1:closing],
                    line_start=line_start,
                    line_end=line_end,
                    offset_start=match.start(),
                    offset_end=closing + 1,
                )
            )
    return tuple(sorted(found, key=lambda item: (item.offset_start, item.offset_end)))
