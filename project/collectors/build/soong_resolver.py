from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from collectors.build.blueprint_lexer import SourceSpan
from collectors.build.blueprint_parser import (
    BlueprintDocument,
    BlueprintExpression,
    BlueprintModule,
)
from collectors.facts.model import (
    BuildModuleFact,
    CandidateFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    RelationFact,
    SourceRange,
)


SEMANTIC_PROFILE = "native-static-v0.1"
EXTRACTOR_VERSION = "0.1"
_CONDITIONAL_PROPERTIES = {
    "arch",
    "product_variables",
    "soong_config_variables",
    "target",
}
_DEPENDENCY_PROPERTIES = {
    "header_libs",
    "libs",
    "proc_macros",
    "rlibs",
    "runtime_libs",
    "rustlibs",
    "shared_libs",
    "static_libs",
    "whole_static_libs",
}


@dataclass(frozen=True)
class SoongResolution:
    modules: tuple[BuildModuleFact, ...]
    relations: tuple[RelationFact, ...]
    candidates: tuple[CandidateFact, ...]
    diagnostics: tuple[DiagnosticFact, ...]


@dataclass(frozen=True)
class _ModuleRecord:
    document: BlueprintDocument
    module: BlueprintModule
    name: str
    raw: Mapping[str, object]
    evaluated: Mapping[str, object]
    unresolved: tuple[str, ...]


class _Unresolved:
    def __init__(self, expression: BlueprintExpression) -> None:
        self.expression = expression


def _raw(expression: BlueprintExpression) -> object:
    return expression.to_plain()


def _evaluate(
    expression: BlueprintExpression,
    variables: Mapping[str, object],
) -> object:
    if expression.kind in {"string", "boolean", "integer"}:
        return expression.value
    if expression.kind == "identifier":
        return variables.get(str(expression.value), _Unresolved(expression))
    if expression.kind == "list":
        values: list[object] = []
        for item in expression.items:
            value = _evaluate(item, variables)
            if isinstance(value, _Unresolved):
                return value
            values.append(value)
        return values
    if expression.kind == "map":
        values: dict[str, object] = {}
        for key, item in expression.entries:
            value = _evaluate(item, variables)
            if isinstance(value, _Unresolved):
                return value
            values[key] = value
        return values
    if expression.kind == "concat":
        values: list[object] = []
        for item in expression.items:
            value = _evaluate(item, variables)
            if isinstance(value, _Unresolved) or not isinstance(value, list):
                return _Unresolved(expression)
            values.extend(value)
        return values
    return _Unresolved(expression)


def _variables(document: BlueprintDocument) -> Mapping[str, object]:
    values: dict[str, object] = {}
    for assignment in document.assignments:
        value = _evaluate(assignment.value, values)
        if assignment.operator == "=":
            values[assignment.name] = value
        elif (
            isinstance(values.get(assignment.name), list)
            and isinstance(value, list)
        ):
            values[assignment.name] = [*values[assignment.name], *value]  # type: ignore[misc]
        else:
            values[assignment.name] = _Unresolved(assignment.value)
    return values


def _source_range(span: SourceSpan) -> SourceRange:
    return SourceRange(
        source_path=span.source_path,
        line_start=span.line_start,
        line_end=span.line_end,
        column_start=span.column_start,
        column_end=span.column_end,
    )


def _repository_for(
    source_path: str,
    repositories: Mapping[str, Mapping[str, object]],
) -> tuple[str, Mapping[str, object]]:
    matches = [
        (path.strip("/"), metadata)
        for path, metadata in repositories.items()
        if source_path == path.strip("/")
        or source_path.startswith(path.strip("/") + "/")
    ]
    if not matches:
        return "unknown", {}
    return max(matches, key=lambda item: len(item[0]))


def _evidence(
    document: BlueprintDocument,
    repositories: Mapping[str, Mapping[str, object]],
    kind: EvidenceKind,
) -> Evidence:
    repository, metadata = _repository_for(document.source_path, repositories)
    return Evidence(
        repository=repository,
        extractor="blueprint-static-resolver",
        extractor_version=EXTRACTOR_VERSION,
        evidence_kind=kind,
        source_revision=(
            str(metadata["revision"]) if metadata.get("revision") else None
        ),
        content_fingerprint=document.content_fingerprint,
        platform_identity=str(metadata.get("platform_identity", "unknown")),
        semantic_profile_version=SEMANTIC_PROFILE,
    )


def _module_properties(
    module: BlueprintModule,
    variables: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object], tuple[str, ...]]:
    raw: dict[str, object] = {}
    evaluated: dict[str, object] = {}
    unresolved: list[str] = []
    for prop in module.properties:
        raw[prop.name] = _raw(prop.value)
        value = _evaluate(prop.value, variables)
        if prop.name in _CONDITIONAL_PROPERTIES or isinstance(value, _Unresolved):
            unresolved.append(prop.name)
            continue
        evaluated[prop.name] = value
    return raw, evaluated, tuple(sorted(set(unresolved)))


def _merge(base: object, override: object) -> object:
    if isinstance(base, list) and isinstance(override, list):
        return [*base, *override]
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _merge(merged[key], value) if key in merged else value
        return merged
    return override


def _module_dir(record: _ModuleRecord) -> PurePosixPath:
    return PurePosixPath(record.document.source_path).parent


def resolve_soong(
    documents: Sequence[BlueprintDocument],
    repositories: Mapping[str, Mapping[str, object]],
) -> SoongResolution:
    records: list[_ModuleRecord] = []
    candidates: list[CandidateFact] = []
    diagnostics: list[DiagnosticFact] = []
    relations: list[RelationFact] = []

    for document in documents:
        variables = _variables(document)
        for blueprint_module in document.modules:
            raw, evaluated, unresolved = _module_properties(
                blueprint_module, variables
            )
            name = evaluated.get("name")
            if isinstance(name, str) and name:
                records.append(
                    _ModuleRecord(
                        document,
                        blueprint_module,
                        name,
                        raw,
                        evaluated,
                        unresolved,
                    )
                )

    by_name: dict[str, list[_ModuleRecord]] = {}
    for record in records:
        by_name.setdefault(record.name, []).append(record)

    def diagnostic(
        record: _ModuleRecord,
        reason: str,
        message: str,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        location = _source_range(record.module.span)
        suffix = f"{location.source_path}:{location.line_start}:{record.name}"
        diagnostics.append(
            DiagnosticFact(
                language="blueprint",
                fact_kind="DIAGNOSTIC",
                logical_identity=f"diagnostic:{reason}:{suffix}",
                category="build_resolution",
                reason_code=reason,
                message=message,
                source_range=location,
                evidence=_evidence(
                    record.document,
                    repositories,
                    EvidenceKind.UNRESOLVED_REFERENCE,
                ),
                properties=properties or {},
            )
        )

    def candidate(
        record: _ModuleRecord,
        kind: str,
        proposed: str | None = None,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        location = _source_range(record.module.span)
        suffix = f"{location.source_path}:{location.line_start}:{record.name}"
        candidates.append(
            CandidateFact(
                language="blueprint",
                fact_kind="EXTRACTION_CANDIDATE",
                logical_identity=f"candidate:{kind}:{suffix}",
                candidate_kind=kind,
                subject_identity=f"soong:module:{record.name}",
                proposed_identity=proposed,
                source_range=location,
                evidence=_evidence(
                    record.document,
                    repositories,
                    EvidenceKind.AMBIGUOUS_CANDIDATE,
                ),
                properties=properties or {},
            )
        )

    unique: dict[str, _ModuleRecord] = {}
    for name, matches in sorted(by_name.items()):
        if len(matches) == 1:
            unique[name] = matches[0]
            continue
        for record in matches:
            diagnostic(
                record,
                "duplicate_soong_module",
                f"module {name!r} is declared more than once",
                {"declaration_count": len(matches)},
            )
            candidate(record, "duplicate_soong_module")

    resolved_cache: dict[str, dict[str, object]] = {}
    invalid_defaults: set[str] = set()

    def resolved_properties(name: str, stack: tuple[str, ...] = ()) -> dict[str, object]:
        if name in resolved_cache:
            return dict(resolved_cache[name])
        record = unique[name]
        if name in stack:
            cycle = (*stack[stack.index(name):], name)
            for cycle_name in sorted(set(cycle)):
                cycle_record = unique[cycle_name]
                invalid_defaults.add(cycle_name)
                diagnostic(
                    cycle_record,
                    "soong_defaults_cycle",
                    "defaults cycle: " + " -> ".join(cycle),
                    {"cycle": cycle},
                )
                candidate(cycle_record, "soong_defaults_cycle")
            return {}
        merged: dict[str, object] = {}
        defaults = record.evaluated.get("defaults", [])
        if isinstance(defaults, list):
            for default_name in defaults:
                if not isinstance(default_name, str):
                    continue
                target = unique.get(default_name)
                if target is None:
                    diagnostic(
                        record,
                        "missing_soong_module",
                        f"defaults module {default_name!r} was not found",
                        {"property": "defaults", "target": default_name},
                    )
                    continue
                inherited = resolved_properties(default_name, (*stack, name))
                merged = _merge(merged, inherited)  # type: ignore[assignment]
        for key, value in record.evaluated.items():
            if key not in {"name", "defaults"}:
                merged[key] = _merge(merged[key], value) if key in merged else value
        resolved_cache[name] = dict(merged)
        return merged

    for name in sorted(unique):
        resolved_properties(name)

    def relation(
        record: _ModuleRecord,
        kind: str,
        target: str,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        source = f"soong:module:{record.name}"
        relations.append(
            RelationFact(
                language="blueprint",
                fact_kind=kind,
                logical_identity=f"{kind}:{source}->{target}",
                from_identity=source,
                to_identity=target,
                source_range=_source_range(record.module.span),
                evidence=_evidence(
                    record.document,
                    repositories,
                    EvidenceKind.RESOLVED_REFERENCE,
                ),
                properties=properties or {},
            )
        )

    def source_relations(
        owner: _ModuleRecord,
        sources: object,
        seen_groups: frozenset[str] = frozenset(),
    ) -> None:
        if not isinstance(sources, list):
            return
        for source in sources:
            if not isinstance(source, str):
                continue
            if source.startswith(":"):
                group_name = source[1:]
                group = unique.get(group_name)
                if (
                    group is None
                    or group.module.module_type != "filegroup"
                    or group_name in seen_groups
                ):
                    continue
                relation(owner, "EXPANDS_FILEGROUP", f"soong:module:{group_name}")
                source_relations(
                    owner,
                    resolved_cache.get(group_name, {}).get("srcs"),
                    seen_groups | {group_name},
                )
                continue
            path = (_module_dir(owner) / source).as_posix()
            relation(owner, "COMPILES_SOURCE", f"file:{path}")

    module_facts: list[BuildModuleFact] = []
    for name, record in sorted(unique.items()):
        resolved = resolved_cache.get(name, {})
        unresolved = set(record.unresolved)
        for property_name in record.unresolved:
            candidate(
                record,
                "unresolved_conditional_property",
                properties={"property": property_name},
            )
        module_facts.append(
            BuildModuleFact(
                language="blueprint",
                fact_kind="SOONG_MODULE",
                logical_identity=f"soong:module:{name}",
                module_name=name,
                module_kind=record.module.module_type,
                source_range=_source_range(record.module.span),
                evidence=_evidence(
                    record.document,
                    repositories,
                    EvidenceKind.BUILD_DECLARATION,
                ),
                properties={
                    "raw_properties": record.raw,
                    "resolved_properties": resolved,
                    "unresolved_expressions": sorted(unresolved),
                },
            )
        )

        defaults = record.evaluated.get("defaults", [])
        if isinstance(defaults, list) and name not in invalid_defaults:
            for default_name in defaults:
                if isinstance(default_name, str) and default_name in unique:
                    relation(
                        record,
                        "USES_DEFAULTS",
                        f"soong:module:{default_name}",
                    )
        for prop_name in sorted(_DEPENDENCY_PROPERTIES):
            if prop_name in unresolved:
                continue
            dependencies = resolved.get(prop_name, [])
            if not isinstance(dependencies, list):
                continue
            for dependency in dependencies:
                if not isinstance(dependency, str):
                    continue
                dependency_name = dependency.removeprefix(":")
                if dependency_name not in unique:
                    diagnostic(
                        record,
                        "missing_soong_module",
                        f"dependency {dependency_name!r} was not found",
                        {"property": prop_name, "target": dependency_name},
                    )
                    continue
                relation(
                    record,
                    "DEPENDS_ON",
                    f"soong:module:{dependency_name}",
                    {"property": prop_name},
                )
        if "srcs" not in unresolved:
            source_relations(record, resolved.get("srcs"))
        if record.module.module_type == "genrule":
            outputs = resolved.get("out", [])
            if isinstance(outputs, list):
                for output in outputs:
                    if isinstance(output, str):
                        path = (_module_dir(record) / output).as_posix()
                        relation(record, "GENERATES", f"build-artifact:{path}")

    return SoongResolution(
        modules=tuple(sorted(module_facts, key=lambda item: item.logical_identity)),
        relations=tuple(sorted(relations, key=lambda item: item.logical_identity)),
        candidates=tuple(sorted(candidates, key=lambda item: item.logical_identity)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: item.logical_identity)),
    )
