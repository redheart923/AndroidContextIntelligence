from __future__ import annotations

import sqlite3
from pathlib import Path

from graph.writer import Edge, GraphWriter, Node
from workspace.native_validation import validate_native_graph
from workspace.schema_migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[2]


def database(tmp_path: Path) -> Path:
    path = tmp_path / "graph.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / "storage/schema.sql").read_text(encoding="utf-8"))
    apply_migrations(path, ROOT / "storage/migrations")
    return path


def test_validation_rejects_duplicate_native_exporters(tmp_path: Path) -> None:
    path = database(tmp_path)
    writer = GraphWriter(path)
    for identity in ("cpp:function:first", "cpp:function:second"):
        writer.upsert_node(Node(identity, "NATIVE_FUNCTION", identity, properties={"export_name": "duplicate"}))
    writer.close()

    report = validate_native_graph(path, (), ())

    assert report.valid is False
    assert any("duplicate native exporter" in item for item in report.errors)


def test_validation_checks_endpoint_matrix_and_candidate_isolation(tmp_path: Path) -> None:
    path = database(tmp_path)
    writer = GraphWriter(path)
    writer.upsert_node(Node("bad:a", "SOONG_MODULE", "a"))
    writer.upsert_node(Node("bad:b", "SOONG_MODULE", "b"))
    writer.upsert_edge(Edge("JNI_BINDS_TO", "bad:a", "bad:b"))
    writer.upsert_node(Node("candidate:x", "EXTRACTION_CANDIDATE", "x"))
    writer.close()

    report = validate_native_graph(path, (), ())

    assert report.valid is False
    assert any("invalid endpoints" in item for item in report.errors)
    assert report.metrics["effective_candidates"] == 0


def test_strict_capability_requires_materialized_evidence(tmp_path: Path) -> None:
    report = validate_native_graph(database(tmp_path), (), ("jni_bindings",))

    assert report.valid is False
    assert "strict capability has no validated evidence: jni_bindings" in report.errors
