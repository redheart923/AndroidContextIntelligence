from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from collectors.permission.model import (
    PermissionDiagnostic,
    PermissionFact,
    PermissionFactKind,
    canonical_json,
    json_value,
)
from graph.writer import Edge, GraphWriter, Node, stable_id


PACKAGE_KINDS = {
    PermissionFactKind.REQUESTS_PERMISSION,
    PermissionFactKind.ALLOWLISTS_PRIVILEGED_PERMISSION,
    PermissionFactKind.DENIES_PRIVILEGED_PERMISSION,
    PermissionFactKind.DEFAULT_GRANTS_PERMISSION,
}
METHOD_KINDS = {
    PermissionFactKind.REQUIRES_PERMISSION,
    PermissionFactKind.CHECKS_PERMISSION,
    PermissionFactKind.ENFORCES_PERMISSION,
}


@dataclass(frozen=True)
class MaterializationSummary:
    edge_counts: dict[str, int]
    diagnostics: tuple[PermissionDiagnostic, ...]


def _permission_properties(facts: tuple[PermissionFact, ...]) -> dict[str, object]:
    declarations: dict[str, list[PermissionFact]] = defaultdict(list)
    for fact in facts:
        if fact.kind is PermissionFactKind.DECLARES_PERMISSION:
            declarations[fact.permission_name].append(fact)
    result: dict[str, object] = {}
    for permission_name, items in declarations.items():
        properties: dict[str, object] = {}
        keys = sorted({key for item in items for key in item.properties})
        for key in keys:
            values = {
                canonical_json(json_value(item.properties[key]))
                for item in items
                if key in item.properties and item.properties[key] is not None
            }
            if len(values) == 1:
                properties[key] = next(
                    item.properties[key]
                    for item in items
                    if key in item.properties and item.properties[key] is not None
                )
        result[permission_name] = properties
    return result


def _edge_properties(fact: PermissionFact) -> dict[str, object]:
    evidence = fact.evidence
    return {
        **dict(fact.properties),
        "fact_identity": fact.identity,
        "repository": evidence.repository,
        "source_revision": evidence.source_revision or "unknown",
        "source_dialect": evidence.source_dialect,
        "source_expression": evidence.source_expression,
        "parser": evidence.parser,
        "parser_version": evidence.parser_version,
    }


def _missing_owner(fact: PermissionFact, message: str) -> PermissionDiagnostic:
    evidence = fact.evidence
    return PermissionDiagnostic(
        category="unresolved_method_owners",
        reason_code="missing_materialization_owner",
        repository=evidence.repository,
        source_path=evidence.source_path,
        line_start=evidence.line_start,
        line_end=evidence.line_end,
        expression=evidence.source_expression,
        message=message,
        properties={"fact_identity": fact.identity},
    )


def materialize_permission_facts(
    writer: GraphWriter,
    facts: Iterable[PermissionFact],
) -> MaterializationSummary:
    ordered = tuple(sorted(facts, key=lambda item: item.identity))
    declaration_properties = _permission_properties(ordered)
    counts: Counter[str] = Counter()
    diagnostics: list[PermissionDiagnostic] = []

    for fact in ordered:
        evidence = fact.evidence
        permission_id = stable_id("PERMISSION", fact.permission_name)
        writer.upsert_node(
            Node(
                node_id=permission_id,
                node_type="PERMISSION",
                display_name=fact.permission_name,
                qualified_name=fact.permission_name,
                properties=declaration_properties.get(fact.permission_name, {}),
                source_path=evidence.source_path,
                line_start=evidence.line_start,
                line_end=evidence.line_end,
                extractor=evidence.parser,
                source_revision=evidence.source_revision,
            )
        )

        if fact.kind is PermissionFactKind.DECLARES_PERMISSION:
            source_id = stable_id("FILE", evidence.source_path)
            writer.upsert_node(
                Node(
                    node_id=source_id,
                    node_type="FILE",
                    display_name=evidence.source_path.rsplit("/", 1)[-1],
                    qualified_name=evidence.source_path,
                    source_path=evidence.source_path,
                    extractor=evidence.parser,
                    source_revision=evidence.source_revision,
                )
            )
        elif fact.kind in PACKAGE_KINDS:
            if not fact.package_name:
                diagnostics.append(_missing_owner(fact, "package fact has no package owner"))
                continue
            source_id = stable_id("ANDROID_PACKAGE", fact.package_name)
            writer.upsert_node(
                Node(
                    node_id=source_id,
                    node_type="ANDROID_PACKAGE",
                    display_name=fact.package_name,
                    qualified_name=fact.package_name,
                    extractor=evidence.parser,
                    source_revision=evidence.source_revision,
                )
            )
        elif fact.kind in METHOD_KINDS:
            source_id = fact.owner_node_id or ""
            exists = writer.c.execute(
                "SELECT 1 FROM node WHERE node_id=?",
                (source_id,),
            ).fetchone()
            if not source_id or exists is None:
                diagnostics.append(_missing_owner(fact, "method owner is absent from graph"))
                continue
        else:
            continue

        writer.upsert_edge(
            Edge(
                edge_type=fact.kind.value,
                from_node_id=source_id,
                to_node_id=permission_id,
                properties=_edge_properties(fact),
                source_path=evidence.source_path,
                line_start=evidence.line_start,
                line_end=evidence.line_end,
                extractor=evidence.parser,
                source_revision=evidence.source_revision,
            )
        )
        counts[fact.kind.value] += 1

    return MaterializationSummary(dict(sorted(counts.items())), tuple(diagnostics))
