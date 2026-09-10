from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from collectors.facts.model import (
    BuildActionFact,
    BuildModuleFact,
    CandidateFact,
    DiagnosticFact,
    Fact,
    InteropBindingFact,
    RelationFact,
    SymbolFact,
)
from graph.writer import Edge, GraphWriter, Node, stable_id


@dataclass(frozen=True)
class MaterializationReport:
    domain: str
    active_nodes: int
    active_edges: int
    candidate_nodes: int
    diagnostic_nodes: int
    fact_counts: dict[str, int]


def _display(identity: str) -> str:
    return identity.rsplit(":", 1)[-1].rsplit("/", 1)[-1]


def _node(fact: SymbolFact | BuildModuleFact | BuildActionFact | CandidateFact | DiagnosticFact) -> Node:
    properties = dict(fact.properties)
    if isinstance(fact, BuildModuleFact):
        properties.update(module_name=fact.module_name, module_kind=fact.module_kind)
    elif isinstance(fact, BuildActionFact):
        properties.update(rule=fact.rule, inputs=fact.inputs, outputs=fact.outputs)
    elif isinstance(fact, CandidateFact):
        properties.update(
            candidate_kind=fact.candidate_kind,
            subject_identity=fact.subject_identity,
            proposed_identity=fact.proposed_identity,
        )
    elif isinstance(fact, DiagnosticFact):
        properties.update(category=fact.category, reason_code=fact.reason_code, message=fact.message)
    location = fact.source_range
    node_type = fact.fact_kind
    return Node(
        node_id=fact.logical_identity,
        node_type=node_type,
        display_name=(fact.module_name if isinstance(fact, BuildModuleFact) else _display(fact.logical_identity)),
        qualified_name=fact.logical_identity,
        properties={
            **properties,
            "language": fact.language,
            "repository": fact.evidence.repository,
            "evidence_kind": fact.evidence.evidence_kind.value,
            "content_fingerprint": fact.evidence.content_fingerprint,
            "semantic_profile_version": fact.evidence.semantic_profile_version,
        },
        source_path=location.source_path,
        line_start=location.line_start,
        line_end=location.line_end,
        extractor=fact.evidence.extractor,
        source_revision=fact.evidence.source_revision,
    )


def _exists(writer: GraphWriter, identity: str) -> bool:
    return writer.c.execute("SELECT 1 FROM node WHERE node_id=?", (identity,)).fetchone() is not None


def _reference(writer: GraphWriter, identity: str, fact: RelationFact) -> None:
    if _exists(writer, identity):
        return
    location = fact.source_range
    writer.upsert_node(
        Node(
            node_id=identity,
            node_type="SOURCE_REFERENCE",
            display_name=_display(identity),
            qualified_name=identity,
            properties={"origin": "relation_endpoint"},
            source_path=location.source_path,
            line_start=location.line_start,
            line_end=location.line_end,
            extractor=fact.evidence.extractor,
            source_revision=fact.evidence.source_revision,
        )
    )


def _candidate_from_unresolved(writer: GraphWriter, fact: RelationFact | InteropBindingFact, reason: str) -> None:
    identity = stable_id("EXTRACTION_CANDIDATE", f"{reason}:{fact.logical_identity}")
    location = fact.source_range
    writer.upsert_node(
        Node(
            node_id=identity,
            node_type="EXTRACTION_CANDIDATE",
            display_name=reason,
            qualified_name=identity,
            properties={"candidate_kind": reason, "fact_identity": fact.logical_identity},
            source_path=location.source_path,
            line_start=location.line_start,
            line_end=location.line_end,
            extractor=fact.evidence.extractor,
            source_revision=fact.evidence.source_revision,
        )
    )


def materialize_typed_facts(
    database: Path,
    facts: Iterable[Fact],
    *,
    domain: str,
) -> MaterializationReport:
    ordered = tuple(sorted(facts, key=lambda item: item.logical_identity))
    writer = GraphWriter(database)
    counts: Counter[str] = Counter()
    active_nodes = candidate_nodes = diagnostic_nodes = active_edges = 0
    try:
        for fact in ordered:
            counts[fact.fact_kind] += 1
            if isinstance(fact, (SymbolFact, BuildModuleFact, BuildActionFact, CandidateFact, DiagnosticFact)):
                writer.upsert_node(_node(fact))
                if isinstance(fact, CandidateFact):
                    candidate_nodes += 1
                elif isinstance(fact, DiagnosticFact):
                    diagnostic_nodes += 1
                else:
                    active_nodes += 1

        for fact in ordered:
            location = fact.source_range
            if isinstance(fact, RelationFact):
                if not _exists(writer, fact.from_identity):
                    _reference(writer, fact.from_identity, fact)
                if not _exists(writer, fact.to_identity):
                    if fact.fact_kind in {
                        "INCLUDES",
                        "IMPORTS",
                        "IMPORTS_C_ABI_SYMBOL",
                        "COMPILES_SOURCE",
                        "GENERATES",
                        "CONSUMES",
                        "PRODUCES",
                        "USES_RULE",
                    }:
                        _reference(writer, fact.to_identity, fact)
                    else:
                        _candidate_from_unresolved(writer, fact, "missing_relation_endpoint")
                        candidate_nodes += 1
                        continue
                writer.upsert_edge(
                    Edge(
                        fact.fact_kind,
                        fact.from_identity,
                        fact.to_identity,
                        properties={**fact.properties, "fact_identity": fact.logical_identity},
                        source_path=location.source_path,
                        line_start=location.line_start,
                        line_end=location.line_end,
                        extractor=fact.evidence.extractor,
                        source_revision=fact.evidence.source_revision,
                    )
                )
                active_edges += 1
            elif isinstance(fact, InteropBindingFact):
                if not _exists(writer, fact.managed_identity) or not _exists(writer, fact.native_identity):
                    _candidate_from_unresolved(writer, fact, "missing_interop_endpoint")
                    candidate_nodes += 1
                    continue
                writer.upsert_edge(
                    Edge(
                        "JNI_BINDS_TO",
                        fact.managed_identity,
                        fact.native_identity,
                        properties={**fact.properties, "fact_identity": fact.logical_identity},
                        source_path=location.source_path,
                        line_start=location.line_start,
                        line_end=location.line_end,
                        extractor=fact.evidence.extractor,
                        source_revision=fact.evidence.source_revision,
                    )
                )
                active_edges += 1
    finally:
        writer.close()
    return MaterializationReport(
        domain,
        active_nodes,
        active_edges,
        candidate_nodes,
        diagnostic_nodes,
        dict(sorted(counts.items())),
    )
