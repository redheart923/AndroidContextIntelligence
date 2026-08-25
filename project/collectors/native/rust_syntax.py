from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from tree_sitter import Node

from collectors.facts.model import (
    CandidateFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    RelationFact,
    SourceRange,
    SymbolFact,
)
from collectors.native.rust_resolver import (
    relative_rust_identity,
    rust_identity,
    rust_path,
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


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _range(node: Node, source_path: str) -> SourceRange:
    return SourceRange(
        source_path=source_path,
        line_start=node.start_point.row + 1,
        line_end=node.end_point.row + 1,
        column_start=node.start_point.column + 1,
        column_end=node.end_point.column + 1,
    )


def _attribute_value(attributes: tuple[str, ...], name: str) -> str | None:
    expression = re.compile(rf"\b{re.escape(name)}\s*=\s*\"([^\"]+)\"")
    for attribute in attributes:
        match = expression.search(attribute)
        if match:
            return match.group(1)
    return None


def _cfg_expression(attributes: tuple[str, ...]) -> str | None:
    for attribute in attributes:
        match = re.search(r"\bcfg\s*\((.*)\)\s*\]", attribute)
        if match:
            return match.group(1).strip()
    return None


def _has_attribute(attributes: tuple[str, ...], name: str) -> bool:
    return any(re.search(rf"\b{re.escape(name)}\b", item) for item in attributes)


def parse_rust_file(
    path: Path,
    crate_context: Mapping[str, object],
    repository: str,
    revision: str | None,
) -> ParseResult:
    crate_name = str(crate_context.get("crate_name", "")).strip()
    if not crate_name:
        raise ValueError("Rust crate context requires crate_name")
    raw_known_cfg = crate_context.get("known_cfg", ())
    known_cfg = {str(item) for item in raw_known_cfg}  # type: ignore[union-attr]
    source = path.read_bytes()
    runtime = load_grammar("rust")
    tree = runtime.parser.parse(source)
    source_path = _workspace_path(path, repository)
    content_fingerprint = hashlib.sha256(source).hexdigest()
    evidence = Evidence(
        repository=repository,
        extractor=runtime.name,
        extractor_version=runtime.package_version,
        evidence_kind=EvidenceKind.SOURCE_DECLARATION,
        source_revision=revision,
        content_fingerprint=content_fingerprint,
        platform_identity="unknown",
        semantic_profile_version=SEMANTIC_PROFILE,
    )
    facts: list[Fact] = []
    diagnostics: list[DiagnosticFact] = []

    def alternate_evidence(kind: EvidenceKind) -> Evidence:
        return Evidence(**{**evidence.__dict__, "evidence_kind": kind})

    def emit_symbol(
        node: Node,
        kind: str,
        category: str,
        modules: tuple[str, ...],
        name: str,
        properties: dict[str, object] | None = None,
        identity: str | None = None,
    ) -> str:
        logical_identity = identity or rust_identity(
            category, crate_name, modules, name
        )
        facts.append(
            SymbolFact(
                language="rust",
                fact_kind=kind,
                logical_identity=logical_identity,
                source_range=_range(node, source_path),
                evidence=evidence,
                properties=properties or {},
            )
        )
        return logical_identity

    def emit_relation(
        node: Node,
        kind: str,
        from_identity: str,
        to_identity: str,
        properties: dict[str, object] | None = None,
    ) -> None:
        facts.append(
            RelationFact(
                language="rust",
                fact_kind=kind,
                logical_identity=f"{kind}:{from_identity}->{to_identity}",
                from_identity=from_identity,
                to_identity=to_identity,
                source_range=_range(node, source_path),
                evidence=evidence,
                properties=properties or {},
            )
        )

    crate_identity = emit_symbol(
        tree.root_node,
        "RUST_CRATE",
        "crate",
        (),
        crate_name,
        identity=f"rust:crate:{crate_name}",
    )

    def visit_container(
        node: Node,
        modules: tuple[str, ...],
        owner: str,
    ) -> None:
        pending: list[str] = []
        for child in node.named_children:
            if child.type == "attribute_item":
                pending.append(_text(child, source))
                continue
            visit(child, modules, owner, tuple(pending))
            pending.clear()

    def visit(
        node: Node,
        modules: tuple[str, ...],
        owner: str,
        attributes: tuple[str, ...] = (),
    ) -> None:
        if node.type == "ERROR" or node.is_missing:
            location = _range(node, source_path)
            diagnostics.append(
                DiagnosticFact(
                    language="rust",
                    fact_kind="DIAGNOSTIC",
                    logical_identity=(
                        f"diagnostic:rust_parse_error:{source_path}:"
                        f"{location.line_start}:{location.column_start}"
                    ),
                    category="parse_error",
                    reason_code="tree_sitter_parse_error",
                    message="Tree-sitter reported invalid or incomplete Rust syntax",
                    source_range=location,
                    evidence=alternate_evidence(EvidenceKind.UNRESOLVED_REFERENCE),
                    properties={"node_type": node.type},
                )
            )
            return

        cfg = _cfg_expression(attributes)
        if cfg is not None and cfg not in known_cfg:
            name = _text(node.child_by_field_name("name"), source) or node.type
            facts.append(
                CandidateFact(
                    language="rust",
                    fact_kind="EXTRACTION_CANDIDATE",
                    logical_identity=(
                        f"candidate:rust_cfg:{source_path}:"
                        f"{node.start_point.row + 1}:{name}"
                    ),
                    candidate_kind="unknown_cfg_item",
                    subject_identity=owner,
                    proposed_identity=rust_path(crate_name, modules, name),
                    source_range=_range(node, source_path),
                    evidence=alternate_evidence(EvidenceKind.AMBIGUOUS_CANDIDATE),
                    properties={"cfg_expression": cfg, "item_type": node.type},
                )
            )
            return

        if node.type == "mod_item":
            name = _text(node.child_by_field_name("name"), source)
            module_identity = emit_symbol(
                node, "RUST_MODULE", "module", modules, name
            )
            emit_relation(node, "CONTAINS", owner, module_identity)
            body = node.child_by_field_name("body")
            if body is not None:
                visit_container(body, (*modules, name), module_identity)
            return

        if node.type == "use_declaration":
            argument = node.child_by_field_name("argument")
            target = _text(argument, source).strip()
            emit_relation(node, "IMPORTS_SYMBOL", owner, target)
            return

        if node.type == "trait_item":
            name = _text(node.child_by_field_name("name"), source)
            trait_identity = emit_symbol(
                node, "RUST_TRAIT", "trait", modules, name
            )
            emit_relation(node, "CONTAINS", owner, trait_identity)
            return

        if node.type in {"struct_item", "enum_item", "type_item", "union_item"}:
            name = _text(node.child_by_field_name("name"), source)
            type_identity = emit_symbol(
                node,
                "RUST_TYPE",
                "type",
                modules,
                name,
                {"declaration_kind": node.type},
            )
            emit_relation(node, "CONTAINS", owner, type_identity)
            return

        if node.type == "impl_item":
            trait_name = _text(node.child_by_field_name("trait"), source).strip()
            type_name = _text(node.child_by_field_name("type"), source).strip()
            type_identity = relative_rust_identity(
                "type", crate_name, modules, type_name
            )
            impl_name = f"{trait_name + '-for-' if trait_name else ''}{type_name}@{node.start_point.row + 1}"
            impl_identity = emit_symbol(
                node,
                "RUST_IMPL",
                "impl",
                modules,
                impl_name,
                {"trait": trait_name or None, "type": type_name},
            )
            emit_relation(node, "IMPLEMENTS_TYPE", impl_identity, type_identity)
            if trait_name:
                emit_relation(
                    node,
                    "IMPLEMENTS_TRAIT",
                    type_identity,
                    relative_rust_identity(
                        "trait", crate_name, modules, trait_name
                    ),
                )
            body = node.child_by_field_name("body")
            if body is not None:
                visit_container(body, modules, type_identity)
            return

        if node.type in {"function_item", "function_signature_item"}:
            name = _text(node.child_by_field_name("name"), source)
            parameters = _text(node.child_by_field_name("parameters"), source)
            owner_path = owner.split(":", 2)[-1]
            function_identity = f"rust:function:{owner_path}::{name}{parameters}"
            function = emit_symbol(
                node,
                "RUST_FUNCTION",
                "function",
                modules,
                name,
                {
                    "abi": "C" if 'extern "C"' in _text(node, source) else None,
                    "declaration_only": node.type == "function_signature_item",
                    "parameters": parameters,
                },
                identity=function_identity,
            )
            emit_relation(node, "CONTAINS", owner, function)
            export_name = _attribute_value(attributes, "export_name")
            if export_name is None and _has_attribute(attributes, "no_mangle"):
                export_name = name
            if export_name is not None:
                emit_relation(
                    node,
                    "EXPORTS_C_ABI_SYMBOL",
                    function,
                    export_name,
                    {"attribute": "export_name" if export_name != name else "no_mangle"},
                )
            link_name = _attribute_value(attributes, "link_name")
            if link_name is not None:
                emit_relation(
                    node,
                    "IMPORTS_C_ABI_SYMBOL",
                    function,
                    link_name,
                    {"attribute": "link_name"},
                )
            return

        if node.type == "foreign_mod_item":
            body = node.child_by_field_name("body")
            if body is not None:
                visit_container(body, modules, owner)
            return

        if node.type == "macro_invocation":
            macro = node.child_by_field_name("macro")
            name = _text(macro, source).strip()
            identity = rust_identity("macro", crate_name, modules, name)
            emit_symbol(
                node,
                "RUST_MACRO_INVOCATION",
                "macro",
                modules,
                name,
                {"expanded": False},
                identity=identity,
            )
            diagnostics.append(
                DiagnosticFact(
                    language="rust",
                    fact_kind="DIAGNOSTIC",
                    logical_identity=f"diagnostic:unexpanded_rust_macro:{identity}",
                    category="unexpanded_macro",
                    reason_code="unexpanded_rust_macro",
                    message="Rust macro invocation was recorded but not expanded",
                    source_range=_range(node, source_path),
                    evidence=alternate_evidence(EvidenceKind.UNRESOLVED_REFERENCE),
                    properties={"macro": name},
                )
            )
            return

        visit_container(node, modules, owner)

    visit_container(tree.root_node, (), crate_identity)
    facts.sort(key=lambda item: item.logical_identity)
    diagnostics.sort(key=lambda item: item.logical_identity)
    return ParseResult(
        facts=tuple(facts),
        diagnostics=tuple(diagnostics),
        grammar_name=runtime.name,
        grammar_fingerprint=runtime.fingerprint,
    )
