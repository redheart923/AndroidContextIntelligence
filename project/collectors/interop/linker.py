from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from collectors.facts.model import (
    CandidateFact,
    DiagnosticFact,
    EvidenceKind,
    InteropBindingFact,
    SymbolFact,
)
from collectors.interop.jni_name_codec import JniNameError, decode_jni_symbol


@dataclass(frozen=True)
class LinkResult:
    bindings: tuple[InteropBindingFact, ...]
    candidates: tuple[CandidateFact, ...]
    diagnostics: tuple[DiagnosticFact, ...]


def _managed_key(item: SymbolFact) -> tuple[str, str, str]:
    return (
        str(item.properties.get("class_name", "")),
        str(item.properties.get("method_name", "")),
        str(item.properties.get("descriptor", "")),
    )


def _arguments(descriptor: str) -> str | None:
    if not descriptor.startswith("(") or ")" not in descriptor:
        return None
    return descriptor[1:descriptor.index(")")]


def _candidate(
    kind: str,
    subject: SymbolFact | CandidateFact,
    proposed: str | None,
    properties: Mapping[str, object] | None = None,
) -> CandidateFact:
    return CandidateFact(
        language="jni",
        fact_kind="EXTRACTION_CANDIDATE",
        logical_identity=f"candidate:{kind}:{subject.logical_identity}:{proposed or 'none'}",
        candidate_kind=kind,
        subject_identity=subject.logical_identity,
        proposed_identity=proposed,
        source_range=subject.source_range,
        evidence=replace(subject.evidence, evidence_kind=EvidenceKind.AMBIGUOUS_CANDIDATE),
        properties=dict(properties or {}),
    )


def _binding(
    managed: SymbolFact,
    native: SymbolFact,
    evidence_source: SymbolFact | CandidateFact,
    evidence_kind: EvidenceKind,
) -> InteropBindingFact:
    return InteropBindingFact(
        language="jni",
        fact_kind="JNI_BINDS",
        logical_identity=f"JNI_BINDS:{managed.logical_identity}->{native.logical_identity}",
        managed_identity=managed.logical_identity,
        native_identity=native.logical_identity,
        source_range=evidence_source.source_range,
        evidence=replace(evidence_source.evidence, evidence_kind=evidence_kind),
        properties={
            "reversible_evidence": True,
            "evidence_type": evidence_kind.value,
        },
    )


def link_interop(
    managed: Iterable[SymbolFact],
    native: Iterable[SymbolFact],
    registrations: Iterable[CandidateFact],
    build_context: Mapping[str, object],
) -> LinkResult:
    del build_context  # Build membership may disambiguate later; it never proves a binding.
    managed_items = tuple(managed)
    native_items = tuple(native)
    registrations = tuple(registrations)
    by_managed = {_managed_key(item): item for item in managed_items}
    by_export: dict[str, list[SymbolFact]] = {}
    for item in native_items:
        by_export.setdefault(str(item.properties.get("export_name", "")), []).append(item)

    bindings: dict[str, InteropBindingFact] = {}
    candidates: list[CandidateFact] = []
    explicit_by_managed: dict[str, str] = {}

    for registration in registrations:
        class_name = str(registration.properties.get("class_name", ""))
        method_name = str(registration.properties.get("method_name", ""))
        descriptor = str(registration.properties.get("descriptor", ""))
        native_symbol = str(registration.properties.get("native_symbol", ""))
        target = by_managed.get((class_name, method_name, descriptor))
        same_method = [
            item for item in managed_items
            if _managed_key(item)[:2] == (class_name, method_name)
        ]
        if target is None:
            kind = "descriptor_mismatch" if same_method else "missing_managed_source"
            candidates.append(_candidate(kind, registration, registration.proposed_identity))
            continue
        native_matches = by_export.get(native_symbol, [])
        if not native_matches:
            candidates.append(_candidate("missing_native_source", registration, target.logical_identity))
            continue
        if len(native_matches) != 1:
            candidates.append(_candidate("ambiguous_native_symbol", registration, target.logical_identity))
            continue
        linked = _binding(target, native_matches[0], registration, EvidenceKind.EXPLICIT_REGISTRATION)
        bindings[linked.logical_identity] = linked
        explicit_by_managed[target.logical_identity] = native_matches[0].logical_identity

    for native_item in native_items:
        export = str(native_item.properties.get("export_name", ""))
        if not export.startswith("Java_"):
            continue
        try:
            decoded = decode_jni_symbol(export)
        except JniNameError:
            candidates.append(_candidate("invalid_direct_jni_name", native_item, None))
            continue
        method_matches = [
            item for item in managed_items
            if _managed_key(item)[:2] == (decoded.class_name, decoded.method_name)
        ]
        if decoded.is_long:
            method_matches = [
                item for item in method_matches
                if _arguments(_managed_key(item)[2]) == decoded.argument_descriptor
            ]
        if not method_matches:
            candidates.append(_candidate("missing_managed_source", native_item, None))
            continue
        if len(method_matches) > 1:
            kind = "ambiguous_short_jni_name" if not decoded.is_long else "ambiguous_long_jni_name"
            candidates.append(_candidate(kind, native_item, None))
            continue
        target = method_matches[0]
        explicit_native = explicit_by_managed.get(target.logical_identity)
        if explicit_native is not None:
            if explicit_native != native_item.logical_identity:
                candidates.append(
                    _candidate(
                        "conflicting_jni_evidence",
                        native_item,
                        target.logical_identity,
                        {"authoritative_native_identity": explicit_native},
                    )
                )
            continue
        linked = _binding(target, native_item, native_item, EvidenceKind.NAMING_CONVENTION)
        bindings[linked.logical_identity] = linked

    return LinkResult(
        bindings=tuple(sorted(bindings.values(), key=lambda item: item.logical_identity)),
        candidates=tuple(sorted(candidates, key=lambda item: item.logical_identity)),
        diagnostics=(),
    )
