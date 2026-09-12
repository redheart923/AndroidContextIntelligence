from __future__ import annotations

import hashlib
from dataclasses import dataclass

from collectors.build.blueprint_lexer import SourceSpan, Token, lex_blueprint


class BlueprintSyntaxError(ValueError):
    """Raised when a Blueprint document is syntactically incomplete."""


@dataclass(frozen=True)
class BlueprintExpression:
    kind: str
    value: object | None
    items: tuple["BlueprintExpression", ...]
    entries: tuple[tuple[str, "BlueprintExpression"], ...]
    span: SourceSpan

    def to_plain(self) -> object:
        if self.kind in {"string", "boolean", "integer", "identifier"}:
            return self.value
        if self.kind == "list":
            return [item.to_plain() for item in self.items]
        if self.kind == "map":
            return {key: value.to_plain() for key, value in self.entries}
        if self.kind == "concat":
            return {
                "kind": "concat",
                "items": [item.to_plain() for item in self.items],
            }
        if self.kind == "call":
            return {
                "kind": "call",
                "name": self.value,
                "arguments": [item.to_plain() for item in self.items],
            }
        raise ValueError(f"unsupported Blueprint expression kind: {self.kind}")


@dataclass(frozen=True)
class BlueprintProperty:
    name: str
    value: BlueprintExpression
    span: SourceSpan


@dataclass(frozen=True)
class BlueprintAssignment:
    name: str
    operator: str
    value: BlueprintExpression
    span: SourceSpan


@dataclass(frozen=True)
class BlueprintModule:
    module_type: str
    properties: tuple[BlueprintProperty, ...]
    span: SourceSpan

    def property(self, name: str) -> BlueprintProperty:
        for item in self.properties:
            if item.name == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True)
class BlueprintDocument:
    source_path: str
    content_fingerprint: str
    assignments: tuple[BlueprintAssignment, ...]
    modules: tuple[BlueprintModule, ...]


def _combined_span(first: SourceSpan, last: SourceSpan) -> SourceSpan:
    return SourceSpan(
        source_path=first.source_path,
        start_offset=first.start_offset,
        end_offset=last.end_offset,
        line_start=first.line_start,
        column_start=first.column_start,
        line_end=last.line_end,
        column_end=last.column_end,
    )


class _Parser:
    def __init__(self, tokens: tuple[Token, ...]) -> None:
        self.tokens = tokens
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def accept(self, kind: str) -> Token | None:
        if self.current.kind != kind:
            return None
        token = self.current
        self.index += 1
        return token

    def expect(self, kind: str) -> Token:
        token = self.accept(kind)
        if token is None:
            current = self.current
            raise BlueprintSyntaxError(
                f"expected {kind}, found {current.kind} at "
                f"{current.span.source_path}:{current.span.line_start}:"
                f"{current.span.column_start}"
            )
        return token

    def expression(self) -> BlueprintExpression:
        first = self.primary()
        items = [first]
        while self.accept("PLUS") is not None:
            items.append(self.primary())
        if len(items) == 1:
            return first
        return BlueprintExpression(
            "concat", None, tuple(items), (),
            _combined_span(items[0].span, items[-1].span),
        )

    def primary(self) -> BlueprintExpression:
        token = self.current
        scalar_kind = {
            "STRING": "string",
            "BOOL": "boolean",
            "INT": "integer",
        }.get(token.kind)
        if scalar_kind is not None:
            self.index += 1
            return BlueprintExpression(scalar_kind, token.value, (), (), token.span)
        if token.kind == "IDENT":
            self.index += 1
            if self.accept("LPAREN") is None:
                return BlueprintExpression(
                    "identifier", token.value, (), (), token.span
                )
            arguments: list[BlueprintExpression] = []
            if self.current.kind != "RPAREN":
                while True:
                    arguments.append(self.expression())
                    if self.accept("COMMA") is None:
                        break
                    if self.current.kind == "RPAREN":
                        break
            close = self.expect("RPAREN")
            return BlueprintExpression(
                "call", token.value, tuple(arguments), (),
                _combined_span(token.span, close.span),
            )
        if (open_token := self.accept("LBRACKET")) is not None:
            items: list[BlueprintExpression] = []
            if self.current.kind != "RBRACKET":
                while True:
                    items.append(self.expression())
                    if self.accept("COMMA") is None:
                        break
                    if self.current.kind == "RBRACKET":
                        break
            close = self.expect("RBRACKET")
            return BlueprintExpression(
                "list", None, tuple(items), (),
                _combined_span(open_token.span, close.span),
            )
        if (open_token := self.accept("LBRACE")) is not None:
            entries: list[tuple[str, BlueprintExpression]] = []
            if self.current.kind != "RBRACE":
                while True:
                    key = self.current
                    if key.kind not in {"IDENT", "STRING", "BOOL"}:
                        self.expect("IDENT")
                    self.index += 1
                    binding: str | None = None
                    if self.accept("AT") is not None:
                        binding = str(self.expect("IDENT").value)
                    self.expect("COLON")
                    key_value = (
                        str(key.value).lower()
                        if key.kind == "BOOL"
                        else str(key.value)
                    )
                    if binding is not None:
                        key_value = f"{key_value} @ {binding}"
                    entries.append((key_value, self.expression()))
                    if self.accept("COMMA") is None:
                        break
                    if self.current.kind == "RBRACE":
                        break
            close = self.expect("RBRACE")
            return BlueprintExpression(
                "map", None, (), tuple(entries),
                _combined_span(open_token.span, close.span),
            )
        raise BlueprintSyntaxError(
            f"expected expression, found {token.kind} at "
            f"{token.span.source_path}:{token.span.line_start}:"
            f"{token.span.column_start}"
        )

    def property(self) -> BlueprintProperty:
        name = self.expect("IDENT")
        self.expect("COLON")
        value = self.expression()
        return BlueprintProperty(
            str(name.value), value, _combined_span(name.span, value.span)
        )

    def module(self, module_type: Token) -> BlueprintModule:
        self.expect("LBRACE")
        properties: list[BlueprintProperty] = []
        while self.current.kind != "RBRACE":
            if self.current.kind == "EOF":
                self.expect("RBRACE")
            properties.append(self.property())
            if self.current.kind == "EOF":
                self.expect("RBRACE")
            if self.accept("COMMA") is None and self.current.kind != "RBRACE":
                self.expect("COMMA")
        close = self.expect("RBRACE")
        return BlueprintModule(
            str(module_type.value), tuple(properties),
            _combined_span(module_type.span, close.span),
        )


def parse_blueprint(text: str, source_path: str) -> BlueprintDocument:
    parser = _Parser(lex_blueprint(text, source_path))
    assignments: list[BlueprintAssignment] = []
    modules: list[BlueprintModule] = []
    while parser.current.kind != "EOF":
        name = parser.expect("IDENT")
        if parser.current.kind == "LBRACE":
            modules.append(parser.module(name))
            continue
        operator = parser.current
        if operator.kind not in {"EQUAL", "PLUS_EQUAL"}:
            parser.expect("EQUAL")
        parser.index += 1
        value = parser.expression()
        assignments.append(
            BlueprintAssignment(
                str(name.value), str(operator.value), value,
                _combined_span(name.span, value.span),
            )
        )
    return BlueprintDocument(
        source_path=source_path,
        content_fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        assignments=tuple(assignments),
        modules=tuple(modules),
    )
