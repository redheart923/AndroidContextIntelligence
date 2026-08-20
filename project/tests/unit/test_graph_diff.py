from __future__ import annotations

import sqlite3
from pathlib import Path

from collectors.codeql.corrections import apply_corrections, parse_correction
from scripts.graph_diff import compare_graphs
from workspace.schema_migrations import apply_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def graph_pair(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "before.db"
    with sqlite3.connect(before) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )
    apply_migrations(before, PROJECT_ROOT / "storage/migrations")
    with sqlite3.connect(before) as connection:
        for node_id in ("a", "b", "c"):
            connection.execute(
                "INSERT INTO node VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    node_id, "JAVA_METHOD", node_id, node_id, "{}", "demo/A.java",
                    1, 1, "abc", "fixture", "1", node_id * 64, "active",
                    "2026-08-20T00:00:00Z",
                ),
            )
        for edge_id, target, digest in (("edge-1", "b", "1" * 64), ("edge-2", "b", "2" * 64)):
            connection.execute(
                "INSERT INTO edge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    edge_id, "CALLS", "a", target, "{}", "demo/A.java", 1, 1,
                    "abc", "fixture", "1", digest, "active", "2026-08-20T00:00:00Z",
                ),
            )
        connection.execute(
            "INSERT INTO extraction_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "run-1", "corrections", "d" * 64, "s" * 64, "aosp", "userdebug",
                "[]", "2.26.3", "java-kotlin", "l" * 64, 1, 1, "complete",
                "2026-08-20T00:00:00Z", "2026-08-20T01:00:00Z", "{}",
            ),
        )
    after = tmp_path / "after.db"
    with sqlite3.connect(before) as source, sqlite3.connect(after) as target:
        source.backup(target)
    suppress = parse_correction(
        {
            "correction_id": "CORR-SUPPRESS",
            "action": "suppress",
            "target_fact_uri": "edge:edge-1",
            "expected_content_hash": "1" * 64,
            "applicable_source_revision": "abc",
            "reason": "false positive",
            "evidence_refs": ["review:1"],
            "author": "a",
            "approved_by": "b",
            "approval_ref": "CR-1",
        }
    )
    replace = parse_correction(
        {
            "correction_id": "CORR-REPLACE",
            "action": "replace",
            "target_fact_uri": "edge:edge-2",
            "expected_content_hash": "2" * 64,
            "applicable_source_revision": "abc",
            "reason": "wrong target",
            "evidence_refs": ["review:2"],
            "author": "a",
            "approved_by": "b",
            "approval_ref": "CR-2",
            "replacement": {
                "fact_kind": "edge",
                "edge_type": "CALLS",
                "from_node_id": "a",
                "to_node_id": "c",
                "properties": {"confidence_class": "reviewed"},
                "source_path": "demo/A.java",
                "line_start": 1,
                "line_end": 1,
            },
        }
    )
    apply_corrections(after, (suppress, replace), source_revision="abc", run_id="run-1")
    return before, after


def test_graph_diff_classifies_suppressed_and_superseded(tmp_path: Path) -> None:
    before, after = graph_pair(tmp_path)

    diff = compare_graphs(before, after)

    assert diff.counts["suppressed"] == 1
    assert diff.counts["superseded"] == 1
    assert "edge:edge-1" in diff.details["suppressed"]
    assert "edge:edge-2" in diff.details["superseded"]
