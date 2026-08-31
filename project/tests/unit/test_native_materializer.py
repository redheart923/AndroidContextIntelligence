from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

from collectors.build.materializer import materialize_build_facts
from collectors.facts.model import (
    BuildModuleFact,
    CandidateFact,
    Evidence,
    EvidenceKind,
    InteropBindingFact,
    RelationFact,
    SourceRange,
    SymbolFact,
)
from collectors.interop.materializer import materialize_interop_facts
from collectors.native.materializer import materialize_native_facts
from workspace.schema_migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[2]
LOCATION = SourceRange("frameworks/native/demo.cpp", 1, 2)


def evidence(kind: EvidenceKind = EvidenceKind.SOURCE_DECLARATION) -> Evidence:
    return Evidence(
        repository="frameworks/native",
        extractor="fixture",
        extractor_version="1",
        evidence_kind=kind,
        source_revision="a" * 40,
        content_fingerprint=hashlib.sha256(b"fixture").hexdigest(),
        platform_identity="android-current",
        semantic_profile_version="native-static-v0.1",
    )


def database(tmp_path: Path) -> Path:
    path = tmp_path / "graph.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / "storage/schema.sql").read_text(encoding="utf-8"))
    apply_migrations(path, ROOT / "storage/migrations")
    return path


def symbol(identity: str, export: str | None = None) -> SymbolFact:
    return SymbolFact(
        language="cpp",
        fact_kind="NATIVE_FUNCTION",
        logical_identity=identity,
        source_range=LOCATION,
        evidence=evidence(),
        properties={"export_name": export or identity.rsplit(":", 1)[-1]},
    )


def test_native_relations_materialize_only_with_auditable_endpoints(tmp_path: Path) -> None:
    path = database(tmp_path)
    function = symbol("cpp:function:demo")
    relation = RelationFact(
        language="cpp",
        fact_kind="INCLUDES",
        logical_identity="include:demo",
        from_identity=function.logical_identity,
        to_identity="frameworks/native/demo.h",
        source_range=LOCATION,
        evidence=evidence(),
        properties={},
    )

    report = materialize_native_facts(path, (relation, function))

    assert report.active_edges == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM effective_edge WHERE edge_type='INCLUDES'").fetchone()[0] == 1


def test_candidate_is_audit_node_and_never_effective(tmp_path: Path) -> None:
    path = database(tmp_path)
    candidate = CandidateFact(
        language="cpp",
        fact_kind="EXTRACTION_CANDIDATE",
        logical_identity="candidate:missing-target",
        candidate_kind="missing_soong_target",
        subject_identity="soong:libdemo",
        proposed_identity="soong:missing",
        source_range=LOCATION,
        evidence=replace(evidence(), evidence_kind=EvidenceKind.AMBIGUOUS_CANDIDATE),
        properties={"reason": "target absent"},
    )

    report = materialize_native_facts(path, (candidate,))

    assert report.candidate_nodes == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM node WHERE node_type='EXTRACTION_CANDIDATE'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM effective_node WHERE node_type='EXTRACTION_CANDIDATE'").fetchone()[0] == 0


def test_build_and_interop_materializers_preserve_domain_edges(tmp_path: Path) -> None:
    path = database(tmp_path)
    module = BuildModuleFact(
        language="blueprint",
        fact_kind="SOONG_MODULE",
        logical_identity="soong:frameworks/native:libdemo",
        module_name="libdemo",
        module_kind="cc_library",
        source_range=SourceRange("frameworks/native/Android.bp", 1, 4),
        evidence=replace(evidence(), evidence_kind=EvidenceKind.BUILD_DECLARATION),
        properties={},
    )
    managed = SymbolFact(
        language="java", fact_kind="MANAGED_NATIVE_DECLARATION",
        logical_identity="managed-native:java:com.example.Demo#run()V",
        source_range=LOCATION, evidence=evidence(), properties={},
    )
    native = symbol("cpp:function:nativeRun")
    binding = InteropBindingFact(
        language="jni", fact_kind="JNI_BINDS",
        logical_identity="jni:demo",
        managed_identity=managed.logical_identity,
        native_identity=native.logical_identity,
        source_range=LOCATION,
        evidence=replace(evidence(), evidence_kind=EvidenceKind.NAMING_CONVENTION),
        properties={"reversible_evidence": True},
    )

    build_report = materialize_build_facts(path, (module,))
    interop_report = materialize_interop_facts(path, (binding, managed, native))

    assert build_report.active_nodes == 1
    assert interop_report.active_edges == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM effective_edge WHERE edge_type='JNI_BINDS_TO'").fetchone()[0] == 1
