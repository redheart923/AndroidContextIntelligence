from __future__ import annotations

import hashlib
from dataclasses import replace

from collectors.facts.model import (
    CandidateFact,
    Evidence,
    EvidenceKind,
    InteropBindingFact,
    SourceRange,
    SymbolFact,
)
from collectors.interop.linker import link_interop


LOCATION = SourceRange("frameworks/base/demo.cpp", 1, 1, 1, 2)


def evidence(kind: EvidenceKind = EvidenceKind.SOURCE_DECLARATION) -> Evidence:
    return Evidence(
        repository="frameworks/base",
        extractor="fixture",
        extractor_version="1",
        evidence_kind=kind,
        source_revision="d" * 40,
        content_fingerprint=hashlib.sha256(b"fixture").hexdigest(),
        platform_identity="android-current",
        semantic_profile_version="native-static-v0.1",
    )


def managed(name: str, descriptor: str) -> SymbolFact:
    return SymbolFact(
        language="java",
        fact_kind="MANAGED_NATIVE_DECLARATION",
        logical_identity=f"managed-native:java:com.example.Demo#{name}{descriptor}",
        source_range=LOCATION,
        evidence=evidence(),
        properties={
            "class_name": "com.example.Demo",
            "method_name": name,
            "descriptor": descriptor,
        },
    )


def native(export_name: str, identity: str | None = None) -> SymbolFact:
    return SymbolFact(
        language="cpp",
        fact_kind="NATIVE_FUNCTION",
        logical_identity=identity or f"cpp:function:{export_name}",
        source_range=LOCATION,
        evidence=evidence(),
        properties={"export_name": export_name},
    )


def registration(
    method: str,
    descriptor: str,
    native_symbol: str,
) -> CandidateFact:
    subject = f"cpp:function:{native_symbol}"
    proposed = f"managed-native:java:com.example.Demo#{method}{descriptor}"
    return CandidateFact(
        language="jni",
        fact_kind="EXTRACTION_CANDIDATE",
        logical_identity=f"candidate:explicit:{method}:{native_symbol}",
        candidate_kind="explicit_jni_registration",
        subject_identity=subject,
        proposed_identity=proposed,
        source_range=LOCATION,
        evidence=replace(evidence(), evidence_kind=EvidenceKind.EXPLICIT_REGISTRATION),
        properties={
            "class_name": "com.example.Demo",
            "method_name": method,
            "descriptor": descriptor,
            "native_symbol": native_symbol,
            "registration_api": "jniRegisterNativeMethods",
        },
    )


def bindings(result):
    return [item for item in result.bindings if isinstance(item, InteropBindingFact)]


def test_explicit_registration_links_exact_descriptor_and_native_symbol() -> None:
    result = link_interop(
        managed=(managed("sum", "(II)I"),),
        native=(native("nativeSum"),),
        registrations=(registration("sum", "(II)I", "nativeSum"),),
        build_context={},
    )

    assert len(bindings(result)) == 1
    binding = bindings(result)[0]
    assert binding.managed_identity.endswith("#sum(II)I")
    assert binding.native_identity == "cpp:function:nativeSum"
    assert binding.evidence.evidence_kind == EvidenceKind.EXPLICIT_REGISTRATION
    assert binding.properties["reversible_evidence"] is True


def test_direct_long_jni_name_matches_overload_by_argument_descriptor() -> None:
    result = link_interop(
        managed=(managed("overloaded", "(I)I"), managed("overloaded", "(Ljava/lang/String;)I")),
        native=(native("Java_com_example_Demo_overloaded__I"),),
        registrations=(),
        build_context={},
    )

    assert len(bindings(result)) == 1
    assert bindings(result)[0].managed_identity.endswith("#overloaded(I)I")


def test_unique_short_name_links_but_overloaded_short_name_is_candidate() -> None:
    unique = link_interop(
        managed=(managed("sum", "(II)I"),),
        native=(native("Java_com_example_Demo_sum"),),
        registrations=(),
        build_context={},
    )
    ambiguous = link_interop(
        managed=(managed("overloaded", "(I)I"), managed("overloaded", "(Ljava/lang/String;)I")),
        native=(native("Java_com_example_Demo_overloaded"),),
        registrations=(),
        build_context={},
    )

    assert len(bindings(unique)) == 1
    assert bindings(ambiguous) == []
    assert any(item.candidate_kind == "ambiguous_short_jni_name" for item in ambiguous.candidates)


def test_descriptor_mismatch_and_missing_managed_source_stay_candidates() -> None:
    mismatch = link_interop(
        managed=(managed("sum", "(II)I"),),
        native=(native("nativeSum"),),
        registrations=(registration("sum", "(I)I", "nativeSum"),),
        build_context={},
    )
    missing = link_interop(
        managed=(),
        native=(native("Java_com_example_Demo_sum"),),
        registrations=(),
        build_context={},
    )

    assert bindings(mismatch) == []
    assert any(item.candidate_kind == "descriptor_mismatch" for item in mismatch.candidates)
    assert bindings(missing) == []
    assert any(item.candidate_kind == "missing_managed_source" for item in missing.candidates)


def test_explicit_registration_wins_and_conflicting_direct_evidence_is_reported() -> None:
    result = link_interop(
        managed=(managed("sum", "(II)I"),),
        native=(
            native("nativeSum"),
            native("Java_com_example_Demo_sum", "cpp:function:directSum"),
        ),
        registrations=(registration("sum", "(II)I", "nativeSum"),),
        build_context={},
    )

    assert [item.native_identity for item in bindings(result)] == ["cpp:function:nativeSum"]
    assert any(item.candidate_kind == "conflicting_jni_evidence" for item in result.candidates)


def test_build_context_alone_never_creates_binding() -> None:
    result = link_interop(
        managed=(managed("sum", "(II)I"),),
        native=(native("unrelated"),),
        registrations=(),
        build_context={
            "managed-native:java:com.example.Demo#sum(II)I": ["cpp:function:unrelated"]
        },
    )

    assert bindings(result) == []
