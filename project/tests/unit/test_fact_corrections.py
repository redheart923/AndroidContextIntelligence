from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from collectors.codeql.corrections import (
    CorrectionError,
    apply_corrections,
    load_corrections,
    parse_correction,
)
from workspace.schema_migrations import apply_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def graph_db(tmp_path: Path) -> tuple[Path, str, str]:
    database = tmp_path / "graph.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8"))
    apply_migrations(database, PROJECT_ROOT / "storage/migrations")
    edge_id = "edge-1"
    content_hash = "a" * 64
    with sqlite3.connect(database) as connection:
        for node_id in ("node-a", "node-b", "node-c"):
            connection.execute(
                "INSERT INTO node VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    node_id, "JAVA_METHOD", node_id, node_id, "{}", "demo/A.java",
                    1, 1, "abc", "fixture", "1", "n" * 64, "active",
                    "2026-08-20T00:00:00+00:00",
                ),
            )
        connection.execute(
            "INSERT INTO edge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                edge_id, "CALLS", "node-a", "node-b", "{}", "demo/A.java",
                5, 5, "abc", "fixture", "1", content_hash, "active",
                "2026-08-20T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO extraction_run VALUES(
              'run-1','corrections',
              'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
              'ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss',
              'aosp','userdebug','[]','2.26.3','java-kotlin',
              'llllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllll',
              1,1,'running','2026-08-20T00:00:00+00:00',NULL,'{}'
            )
            """
        )
    return database, edge_id, content_hash


def correction_dict(
    *,
    correction_id: str = "CORR-001",
    action: str = "suppress",
    edge_id: str = "edge-1",
    expected_hash: str = "a" * 64,
) -> dict[str, object]:
    return {
        "correction_id": correction_id,
        "action": action,
        "target_fact_uri": f"edge:{edge_id}",
        "expected_content_hash": expected_hash,
        "applicable_source_revision": "abc",
        "reason": "Reviewed against source and test evidence",
        "evidence_refs": ["review:123"],
        "author": "developer@example.com",
        "approved_by": "reviewer@example.com",
        "approval_ref": "CR-123",
    }


def scalar(database: Path, sql: str, parameters: tuple[object, ...] = ()) -> object:
    with sqlite3.connect(database) as connection:
        return connection.execute(sql, parameters).fetchone()[0]


def test_suppress_retains_raw_fact_and_hides_effective_fact(tmp_path: Path) -> None:
    database, edge_id, content_hash = graph_db(tmp_path)
    correction = parse_correction(correction_dict(edge_id=edge_id, expected_hash=content_hash))

    report = apply_corrections(database, (correction,), source_revision="abc", run_id="run-1")

    assert report.applications[0].status == "applied"
    assert scalar(database, "SELECT status FROM edge WHERE edge_id=?", (edge_id,)) == "active"
    assert scalar(database, "SELECT COUNT(*) FROM effective_edge WHERE edge_id=?", (edge_id,)) == 0


def test_revision_or_hash_mismatch_marks_correction_stale(tmp_path: Path) -> None:
    database, edge_id, _ = graph_db(tmp_path)
    correction = parse_correction(correction_dict(edge_id=edge_id, expected_hash="0" * 64))

    report = apply_corrections(database, (correction,), source_revision="abc", run_id="run-1")

    assert report.applications[0].status == "stale"
    assert scalar(database, "SELECT COUNT(*) FROM effective_edge WHERE edge_id=?", (edge_id,)) == 1


@pytest.mark.parametrize("action", ["replace", "add"])
def test_replacement_payload_must_be_complete(action: str) -> None:
    with pytest.raises(CorrectionError, match="replacement payload"):
        parse_correction(correction_dict(action=action))


def test_replace_inserts_reviewed_edge_and_preserves_raw_evidence(tmp_path: Path) -> None:
    database, edge_id, content_hash = graph_db(tmp_path)
    value = correction_dict(action="replace", edge_id=edge_id, expected_hash=content_hash)
    value["replacement"] = {
        "fact_kind": "edge",
        "edge_type": "CALLS",
        "from_node_id": "node-a",
        "to_node_id": "node-c",
        "properties": {"confidence_class": "reviewed"},
        "source_path": "demo/A.java",
        "line_start": 5,
        "line_end": 5,
    }
    correction = parse_correction(value)

    report = apply_corrections(database, (correction,), source_revision="abc", run_id="run-1")

    assert report.applications[0].status == "applied"
    assert scalar(database, "SELECT COUNT(*) FROM edge WHERE edge_id=?", (edge_id,)) == 1
    assert scalar(database, "SELECT COUNT(*) FROM effective_edge WHERE edge_id=?", (edge_id,)) == 0
    assert scalar(database, "SELECT COUNT(*) FROM effective_edge WHERE extractor='correction'") == 1
    assert scalar(database, "SELECT COUNT(*) FROM edge WHERE edge_type='SUPERSEDES'") == 1
    assert scalar(database, "SELECT COUNT(*) FROM edge WHERE edge_type='CONTRADICTS'") == 1


def test_annotate_overlays_review_without_changing_endpoints(tmp_path: Path) -> None:
    database, edge_id, content_hash = graph_db(tmp_path)
    value = correction_dict(action="annotate", edge_id=edge_id, expected_hash=content_hash)
    value["replacement"] = {
        "confidence_class": "reviewed_high",
        "explanation": "Confirmed by source owner",
    }

    apply_corrections(
        database, (parse_correction(value),), source_revision="abc", run_id="run-1"
    )

    annotation = scalar(
        database,
        "SELECT correction_annotation_json FROM effective_edge WHERE edge_id=?",
        (edge_id,),
    )
    assert "reviewed_high" in str(annotation)
    assert scalar(database, "SELECT to_node_id FROM edge WHERE edge_id=?", (edge_id,)) == "node-b"


def test_load_rejects_duplicate_ids_and_unknown_keys(tmp_path: Path) -> None:
    root = tmp_path / "corrections"
    root.mkdir()
    text = """
[[corrections]]
correction_id = "CORR-001"
action = "suppress"
target_fact_uri = "edge:edge-1"
expected_content_hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
applicable_source_revision = "abc"
reason = "reviewed"
evidence_refs = ["review:1"]
author = "a"
approved_by = "b"
approval_ref = "CR-1"
"""
    (root / "one.toml").write_text(text, encoding="utf-8")
    (root / "two.toml").write_text(text, encoding="utf-8")
    with pytest.raises(CorrectionError, match="duplicate correction_id"):
        load_corrections(root)

    invalid = correction_dict()
    invalid["unknown"] = True
    with pytest.raises(CorrectionError, match="unknown correction keys"):
        parse_correction(invalid)


def test_idempotent_replay_keeps_one_application(tmp_path: Path) -> None:
    database, edge_id, content_hash = graph_db(tmp_path)
    correction = parse_correction(correction_dict(edge_id=edge_id, expected_hash=content_hash))

    apply_corrections(database, (correction,), source_revision="abc", run_id="run-1")
    apply_corrections(database, (correction,), source_revision="abc", run_id="run-1")

    assert scalar(database, "SELECT COUNT(*) FROM correction_application") == 1
