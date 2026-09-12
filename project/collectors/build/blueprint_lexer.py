from __future__ import annotations

from dataclasses import dataclass


class BlueprintLexError(ValueError):
    """Raised when an Android Blueprint token cannot be completed safely."""


@dataclass(frozen=True)
class SourceSpan:
    source_path: str
    start_offset: int
    end_offset: int
    line_start: int
    column_start: int
    line_end: int
    column_end: int


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    span: SourceSpan


_PUNCTUATION = {
    "{": "LBRACE",
    "}": "RBRACE",
    "[": "LBRACKET",
    "]": "RBRACKET",
    "(": "LPAREN",
    ")": "RPAREN",
    ":": "COLON",
    ",": "COMMA",
    "=": "EQUAL",
    "+": "PLUS",
    "@": "AT",
}


def lex_blueprint(text: str, source_path: str) -> tuple[Token, ...]:
    tokens: list[Token] = []
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

    def span(start: tuple[int, int, int]) -> SourceSpan:
        return SourceSpan(
            source_path=source_path,
            start_offset=start[0],
            end_offset=offset,
            line_start=start[1],
            column_start=start[2],
            line_end=line,
            column_end=column,
        )

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
            start = (offset, line, column)
            advance()
            advance()
            while offset < len(text) and not text.startswith("*/", offset):
                advance()
            if offset == len(text):
                raise BlueprintLexError(
                    f"unterminated block comment at {source_path}:"
                    f"{start[1]}:{start[2]}"
                )
            advance()
            advance()
            continue

        start = (offset, line, column)
        if text.startswith("+=", offset):
            advance()
            advance()
            tokens.append(Token("PLUS_EQUAL", "+=", span(start)))
            continue
        if current in _PUNCTUATION:
            advance()
            tokens.append(Token(_PUNCTUATION[current], current, span(start)))
            continue
        if current == '"':
            advance()
            decoded: list[str] = []
            while offset < len(text) and text[offset] != '"':
                if text[offset] == "\n":
                    raise BlueprintLexError(
                        f"unterminated string at {source_path}:"
                        f"{start[1]}:{start[2]}"
                    )
                if text[offset] != "\\":
                    decoded.append(advance())
                    continue
                advance()
                if offset == len(text):
                    break
                escape = advance()
                decoded.append(
                    {
                        "n": "\n",
                        "r": "\r",
                        "t": "\t",
                        "\\": "\\",
                        '"': '"',
                    }.get(escape, escape)
                )
            if offset == len(text):
                raise BlueprintLexError(
                    f"unterminated string at {source_path}:"
                    f"{start[1]}:{start[2]}"
                )
            advance()
            tokens.append(Token("STRING", "".join(decoded), span(start)))
            continue
        if current.isdigit() or (
            current == "-" and offset + 1 < len(text) and text[offset + 1].isdigit()
        ):
            digits = [advance()]
            while offset < len(text) and text[offset].isdigit():
                digits.append(advance())
            tokens.append(Token("INT", int("".join(digits)), span(start)))
            continue
        if current.isalpha() or current == "_":
            value = [advance()]
            while offset < len(text) and (
                text[offset].isalnum() or text[offset] in "_.-"
            ):
                value.append(advance())
            identifier = "".join(value)
            if identifier in {"true", "false"}:
                tokens.append(Token("BOOL", identifier == "true", span(start)))
            else:
                tokens.append(Token("IDENT", identifier, span(start)))
            continue
        raise BlueprintLexError(
            f"unexpected character {current!r} at "
            f"{source_path}:{line}:{column}"
        )

    eof = SourceSpan(source_path, offset, offset, line, column, line, column)
    tokens.append(Token("EOF", "", eof))
    return tuple(tokens)
