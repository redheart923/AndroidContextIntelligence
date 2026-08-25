from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from collectors.facts.codec import read_facts, write_facts
from collectors.facts.identity import fact_identity
from collectors.facts.model import (
    BuildActionFact,
    BuildModuleFact,
    CandidateFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    InteropBindingFact,
    RelationFact,
    SourceRange,
    SymbolFact,
)


def location(path: str = r"frameworks\native\demo\Foo.cpp") -> SourceRange:
    return SourceRange(
        source_path=path,
        line_start=7,
        line_end=9,
        column_start=3,
        column_end=18,
    )


def evidence() -> Evidence:
    return Evidence(
        repository="frameworks/native",
        extractor="tree-sitter-cpp",
        extractor_version="0.23.4",
        evidence_kind=EvidenceKind.SOURCE_DECLARATION,
        source_revision="0123456789abcdef0123456789abcdef01234567",
        content_fingerprint="a" * 64,
        platform_identity="android-current",
        semantic_profile_version="native-static-v0.1",
    )


def sample_symbol() -> SymbolFact:
    return SymbolFact(
        "cpp",
        "CPP_FUNCTION",
        "android::start(int)",
        location(),
        evidence(),
        {"flags": ["public", "inline"]},
    )


def test_fact_identity_is_order_independent_for_set_like_properties() -> None:
    left = SymbolFact(
        "cpp",
        "CPP_FUNCTION",
        "android::start",
        location(),
        evidence(),
        {"flags": ["b", "a"], "metadata": {"z": 1, "a": 2}},
    )
    right = replace(
        left,
        properties={"metadata": {"a": 2, "z": 1}, "flags": ["a", "b"]},
    )

    assert fact_identity(left) == fact_identity(right)


def test_jsonl_round_trip_preserves_typed_fact(tmp_path: Path) -> None:
    path = tmp_path / "facts.jsonl"

    digest = write_facts(path, [sample_symbol()])

    assert read_facts(path) == (sample_symbol(),)
    assert len(digest) == 64
    assert path.read_bytes().endswith(b"\n")


def test_jsonl_output_is_stable_when_facts_arrive_in_reverse_order(
    tmp_path: Path,
) -> None:
    symbol = sample_symbol()
    relation = RelationFact(
        language="cpp",
        fact_kind="INCLUDES",
        logical_identity="android::start->demo/Foo.h",
        from_identity=symbol.logical_identity,
        to_identity="demo/Foo.h",
        source_range=location(),
        evidence=evidence(),
        properties={"system": False},
    )
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"

    first_digest = write_facts(first, [symbol, relation])
    second_digest = write_facts(second, [relation, symbol])

    assert first_digest == second_digest
    assert first.read_bytes() == second.read_bytes()


def test_source_range_normalizes_windows_separators() -> None:
    value = location()

    assert value.source_path == PurePosixPath(
        "frameworks/native/demo/Foo.cpp"
    ).as_posix()


def test_evidence_kind_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="unknown evidence kind"):
        replace(evidence(), evidence_kind="guessed")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "fact",
    [
        BuildModuleFact(
            language="blueprint",
            fact_kind="SOONG_MODULE",
            logical_identity="frameworks/native:libdemo",
            module_name="libdemo",
            module_kind="cc_library",
            source_range=location("frameworks/native/Android.bp"),
            evidence=replace(
                evidence(), evidence_kind=EvidenceKind.BUILD_DECLARATION
            ),
            properties={"srcs": ["b.cpp", "a.cpp"]},
        ),
        BuildActionFact(
            language="ninja",
            fact_kind="BUILD_ACTION",
            logical_identity="out/demo.o",
            rule="clang++",
            inputs=("b.cpp", "a.cpp"),
            outputs=("out/demo.o",),
            source_range=location("out/build.ninja"),
            evidence=replace(
                evidence(), evidence_kind=EvidenceKind.GENERATED_BUILD_ARTIFACT
            ),
            properties={},
        ),
        InteropBindingFact(
            language="jni",
            fact_kind="JNI_BINDS",
            logical_identity="com.example.Demo#run()V->Java_com_example_Demo_run",
            managed_identity="com.example.Demo#run()V",
            native_identity="Java_com_example_Demo_run",
            source_range=location(),
            evidence=replace(
                evidence(), evidence_kind=EvidenceKind.NAMING_CONVENTION
            ),
            properties={},
        ),
        CandidateFact(
            language="jni",
            fact_kind="EXTRACTION_CANDIDATE",
            logical_identity="candidate:demo",
            candidate_kind="ambiguous_jni_binding",
            subject_identity="com.example.Demo#run()V",
            proposed_identity="native::run",
            source_range=location(),
            evidence=replace(
                evidence(), evidence_kind=EvidenceKind.AMBIGUOUS_CANDIDATE
            ),
            properties={"reasons": ["overload", "missing_descriptor"]},
        ),
        DiagnosticFact(
            language="cpp",
            fact_kind="DIAGNOSTIC",
            logical_identity="diagnostic:demo",
            category="parse_error",
            reason_code="unsupported_syntax",
            message="unsupported declaration",
            source_range=location(),
            evidence=replace(
                evidence(), evidence_kind=EvidenceKind.UNRESOLVED_REFERENCE
            ),
            properties={},
        ),
    ],
)
def test_jsonl_round_trip_supports_every_specialized_fact(
    tmp_path: Path,
    fact: object,
) -> None:
    path = tmp_path / "typed.jsonl"

    write_facts(path, [fact])  # type: ignore[list-item]

    assert read_facts(path) == (fact,)
