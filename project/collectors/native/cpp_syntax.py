from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tree_sitter import Node

from collectors.facts.model import (
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    RelationFact,
    SourceRange,
    SymbolFact,
)
from collectors.native.identity import (
    logical_symbol_identity,
    normalize_cpp_scope,
    normalize_signature,
)
from collectors.native.treesitter_runtime import load_grammar


SEMANTIC_PROFILE = "native-static-v0.1"


@dataclass(frozen=True)
class ParseResult:
    facts: tuple[Fact, ...]
    diagnostics: tuple[DiagnosticFact, ...]
    grammar_name: str
    grammar_fingerprint: str


def _workspace_path(path: Path, repository: str) -> str:
    raw = path.as_posix()
    marker = f"/{repository.strip('/')}/"
    searchable = f"/{raw.lstrip('/')}"
    if marker in searchable:
        return repository.strip("/") + "/" + searchable.split(marker, 1)[1]
    return PurePosixPath(raw).as_posix()


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _range(node: Node, source_path: str) -> SourceRange:
    return SourceRange(
        source_path=source_path,
        line_start=node.start_point.row + 1,
        line_end=node.end_point.row + 1,
        column_start=node.start_point.column + 1,
        column_end=node.end_point.column + 1,
    )


def _named_descendant(node: Node, kinds: set[str]) -> Node | None:
    if node.type in kinds:
        return node
    for child in node.named_children:
        found = _named_descendant(child, kinds)
        if found is not None:
            return found
    return None


def _type_name(node: Node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is None:
        name = _named_descendant(
            node,
            {"type_identifier", "identifier", "qualified_identifier"},
        )
    return _text(name, source).strip() if name is not None else None


def _qualified(scope: tuple[str, ...], name: str) -> str:
    normalized_name = normalize_cpp_scope(name)
    if "::" in normalized_name or not scope:
        return normalized_name
    return "::".join((*scope, normalized_name))


def _relation(
    language: str,
    kind: str,
    from_identity: str,
    to_identity: str,
    node: Node,
    source_path: str,
    evidence: Evidence,
    properties: dict[str, object] | None = None,
) -> RelationFact:
    return RelationFact(
        language=language,
        fact_kind=kind,
        logical_identity=f"{kind}:{from_identity}->{to_identity}",
        from_identity=from_identity,
        to_identity=to_identity,
        source_range=_range(node, source_path),
        evidence=evidence,
        properties=properties or {},
    )


def parse_cpp_file(
    path: Path,
    language: str,
    repository: str,
    revision: str | None,
) -> ParseResult:
    if language not in {"c", "cpp"}:
        raise ValueError(f"unsupported C/C++ syntax language: {language}")
    source = path.read_bytes()
    runtime = load_grammar(language)
    tree = runtime.parser.parse(source)
    source_path = _workspace_path(path, repository)
    evidence = Evidence(
        repository=repository,
        extractor=runtime.name,
        extractor_version=runtime.package_version,
        evidence_kind=EvidenceKind.SOURCE_DECLARATION,
        source_revision=revision,
        content_fingerprint=hashlib.sha256(source).hexdigest(),
        platform_identity="unknown",
        semantic_profile_version=SEMANTIC_PROFILE,
    )
    facts: list[Fact] = []
    diagnostics: list[DiagnosticFact] = []
    file_identity = f"file:{source_path}"

    def emit_symbol(
        node: Node,
        fact_kind: str,
        category: str,
        qualified_name: str,
        properties: dict[str, object],
    ) -> str:
        identity = logical_symbol_identity(language, category, qualified_name)
        facts.append(
            SymbolFact(
                language=language,
                fact_kind=fact_kind,
                logical_identity=identity,
                source_range=_range(node, source_path),
                evidence=evidence,
                properties=properties,
            )
        )
        return identity

    def visit(node: Node, namespace: tuple[str, ...], owner: str | None) -> None:
        if node.type == "ERROR" or node.is_missing:
            location = _range(node, source_path)
            diagnostics.append(
                DiagnosticFact(
                    language=language,
                    fact_kind="DIAGNOSTIC",
                    logical_identity=(
                        f"diagnostic:tree_sitter_parse_error:{source_path}:"
                        f"{location.line_start}:{location.column_start}"
                    ),
                    category="parse_error",
                    reason_code="tree_sitter_parse_error",
                    message="Tree-sitter reported invalid or incomplete syntax",
                    source_range=location,
                    evidence=Evidence(
                        **{
                            **evidence.__dict__,
                            "evidence_kind": EvidenceKind.UNRESOLVED_REFERENCE,
                        }
                    ),
                    properties={"node_type": node.type},
                )
            )
            return

        if node.type == "preproc_include":
            target_node = node.child_by_field_name("path")
            raw_target = _text(target_node, source) if target_node else _text(node, source)
            target = raw_target.strip().strip('<>"')
            if target_node is None:
                match = re.search(r"#\s*include\s*[<\"]([^>\"]+)", raw_target)
                target = match.group(1) if match else raw_target
            facts.append(
                _relation(
                    language,
                    "INCLUDES",
                    file_identity,
                    target,
                    node,
                    source_path,
                    evidence,
                    {"system": "<" in raw_target},
                )
            )
            return

        if node.type == "namespace_definition":
            name_node = node.child_by_field_name("name")
            name = normalize_cpp_scope(_text(name_node, source) if name_node else "")
            next_namespace = (*namespace, *tuple(part for part in name.split("::") if part))
            qualified_name = "::".join(next_namespace)
            namespace_identity = emit_symbol(
                node,
                "CPP_NAMESPACE",
                "namespace",
                qualified_name,
                {},
            )
            if owner is not None:
                facts.append(
                    _relation(
                        language,
                        "CONTAINS",
                        owner,
                        namespace_identity,
                        node,
                        source_path,
                        evidence,
                    )
                )
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    visit(child, next_namespace, namespace_identity)
            return

        if node.type in {"class_specifier", "struct_specifier", "union_specifier"}:
            name = _type_name(node, source)
            if not name:
                return
            qualified_name = _qualified(namespace, name)
            type_identity = emit_symbol(
                node,
                "CPP_TYPE" if language == "cpp" else "C_TYPE",
                "type",
                qualified_name,
                {"declaration_kind": node.type},
            )
            if owner is not None:
                facts.append(
                    _relation(
                        language,
                        "CONTAINS",
                        owner,
                        type_identity,
                        node,
                        source_path,
                        evidence,
                    )
                )
            base_clause = next(
                (child for child in node.named_children if child.type == "base_class_clause"),
                None,
            )
            if base_clause is not None and not base_clause.has_error:
                for child in base_clause.named_children:
                    if child.type == "access_specifier":
                        continue
                    base = _text(child, source).strip()
                    if not base:
                        continue
                    base_name = _qualified(namespace, base)
                    facts.append(
                        _relation(
                            language,
                            "EXTENDS",
                            type_identity,
                            logical_symbol_identity(language, "type", base_name),
                            child,
                            source_path,
                            evidence,
                        )
                    )
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    visit(child, namespace, type_identity)
            return

        if node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            if declarator is None:
                return
            qualified_declarator = _named_descendant(
                declarator,
                {"qualified_identifier"},
            )
            name_node = _named_descendant(
                declarator,
                {"field_identifier", "identifier", "operator_name", "destructor_name"},
            )
            parameters = _named_descendant(declarator, {"parameter_list"})
            if name_node is None:
                return
            raw_name = _text(name_node, source).strip()
            signature = normalize_signature(_text(parameters, source) if parameters else "")
            owner_is_type = owner is not None and owner.startswith("cpp:type:")
            is_qualified_method = language == "cpp" and qualified_declarator is not None
            is_method = language == "cpp" and (owner_is_type or is_qualified_method)
            if is_qualified_method:
                declared_name = normalize_cpp_scope(
                    _text(qualified_declarator, source).strip()
                )
                namespace_name = "::".join(namespace)
                if namespace_name and not declared_name.startswith(
                    namespace_name + "::"
                ):
                    declared_name = f"{namespace_name}::{declared_name}"
                qualified_name = f"{declared_name}{signature}"
            elif owner_is_type:
                owner_name = owner.split(":", 2)[-1]
                qualified_name = f"{owner_name}::{raw_name}{signature}"
            else:
                qualified_name = f"{_qualified(namespace, raw_name)}{signature}"
            function_identity = emit_symbol(
                node,
                "CPP_METHOD" if is_method else ("CPP_FUNCTION" if language == "cpp" else "C_FUNCTION"),
                "method" if is_method else "function",
                qualified_name,
                {
                    "return_type": (
                        _text(node.child_by_field_name("type"), source).strip()
                        if node.child_by_field_name("type")
                        else None
                    ),
                    "signature": signature,
                },
            )
            if owner is not None:
                facts.append(
                    _relation(
                        language,
                        "CONTAINS",
                        owner,
                        function_identity,
                        node,
                        source_path,
                        evidence,
                    )
                )
            return

        for child in node.named_children:
            visit(child, namespace, owner)

    visit(tree.root_node, (), None)
    facts.sort(key=lambda item: item.logical_identity)
    diagnostics.sort(key=lambda item: item.logical_identity)
    return ParseResult(
        facts=tuple(facts),
        diagnostics=tuple(diagnostics),
        grammar_name=runtime.name,
        grammar_fingerprint=runtime.fingerprint,
    )
