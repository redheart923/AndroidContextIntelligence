from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from collectors.permission.report import REQUIRED_REPORT_KEYS
from graph.writer import Edge, GraphWriter, Node
from workspace.permission_validation import (
    PermissionValidationError,
    permission_semantic_fingerprint,
    validate_permission_database,
    validate_permission_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )


def valid_report(path: Path) -> None:
    payload: dict[str, object] = {key: [] for key in REQUIRED_REPORT_KEYS}
    payload.update(
        {
            "schema_version": "1.0",
            "parser_version": "1.0",
            "source_revisions": {"frameworks/base": "a" * 40},
            "repositories_scanned": ["frameworks/base"],
            "files_scanned_by_language": {"xml": 1},
            "xml_candidates_by_dialect": {"manifest": 1},
            "xml_documents_parsed_by_dialect": {"manifest": 1},
            "facts_and_edges_by_type": {"REQUESTS_PERMISSION": 1},
            "duplicate_facts": 0,
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")


def seed_request(database: Path, *, reverse: bool = False) -> None:
    writer = GraphWriter(database)
    nodes = (
        Node(
            node_id="ANDROID_PACKAGE:com.example",
            node_type="ANDROID_PACKAGE",
            qualified_name="com.example",
            display_name="com.example",
            extractor="fixture",
        ),
        Node(
            node_id="PERMISSION:android.permission.CAMERA",
            node_type="PERMISSION",
            qualified_name="android.permission.CAMERA",
            display_name="android.permission.CAMERA",
            extractor="fixture",
        ),
    )
    for node in reversed(nodes) if reverse else nodes:
        writer.upsert_node(node)
    writer.upsert_edge(
        Edge(
            edge_type="REQUESTS_PERMISSION",
            from_node_id="ANDROID_PACKAGE:com.example",
            to_node_id="PERMISSION:android.permission.CAMERA",
            properties={"repository": "frameworks/base"},
            source_path="frameworks/base/AndroidManifest.xml",
            line_start=4,
            line_end=4,
            extractor="fixture",
            source_revision="a" * 40,
        )
    )
    writer.close()


def test_report_validation_rejects_missing_required_key(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    valid_report(report)
    payload = json.loads(report.read_text(encoding="utf-8"))
    del payload["task_failures"]
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PermissionValidationError, match="missing report keys"):
        validate_permission_report(report)


def test_database_validation_rejects_wrong_endpoint_types(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    writer = GraphWriter(database)
    writer.upsert_node(Node("FILE:bad", "FILE", "bad", extractor="fixture"))
    writer.upsert_node(
        Node("PERMISSION:camera", "PERMISSION", "camera", extractor="fixture")
    )
    writer.upsert_edge(
        Edge("REQUESTS_PERMISSION", "FILE:bad", "PERMISSION:camera", extractor="fixture")
    )
    writer.close()

    with pytest.raises(PermissionValidationError, match="endpoint type"):
        validate_permission_database(database)


def test_database_validation_rejects_duplicate_active_semantic_edges(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    seed_request(database)
    with sqlite3.connect(database) as connection:
        original = connection.execute(
            "SELECT * FROM edge WHERE edge_type='REQUESTS_PERMISSION'"
        ).fetchone()
        duplicate = list(original)
        duplicate[0] = "duplicate-edge-id"
        connection.execute(
            f"INSERT INTO edge VALUES ({','.join('?' for _ in duplicate)})",
            duplicate,
        )

    with pytest.raises(PermissionValidationError, match="duplicate active"):
        validate_permission_database(database)


def test_database_validation_rejects_foreign_key_errors(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO edge VALUES (
              'broken', 'REQUESTS_PERMISSION', 'ANDROID_PACKAGE:missing',
              'PERMISSION:missing', '{}', NULL, NULL, NULL, 'unknown',
              'fixture', '1', '', 'active', '2026-07-22T00:00:00Z'
            )
            """
        )

    with pytest.raises(PermissionValidationError, match="foreign key"):
        validate_permission_database(database)


def test_permission_fingerprint_is_independent_of_insertion_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    create_database(first)
    create_database(second)
    seed_request(first)
    seed_request(second, reverse=True)

    assert permission_semantic_fingerprint(first) == permission_semantic_fingerprint(
        second
    )


def test_valid_permission_report_and_database_pass(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    report = tmp_path / "report.json"
    create_database(database)
    seed_request(database)
    valid_report(report)

    validate_permission_report(report)
    validate_permission_database(database)


def test_require_aosp_evidence_rejects_incomplete_fixture(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    seed_request(database)

    with pytest.raises(PermissionValidationError, match="AOSP permission evidence"):
        validate_permission_database(database, require_aosp_evidence=True)
