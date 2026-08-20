from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from workspace.coverage_validation import (
    RuntimeCoverageError,
    evaluate_runtime_coverage,
    validate_runtime_coverage,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE node (
              node_id TEXT PRIMARY KEY,
              node_type TEXT NOT NULL,
              source_path TEXT,
              properties_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE edge (
              edge_id TEXT PRIMARY KEY,
              edge_type TEXT NOT NULL,
              source_path TEXT,
              properties_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )


def _plan(*, strict: bool = False) -> dict[str, object]:
    return {
        "strict": strict,
        "strict_capability": "symbols" if strict else None,
        "repositories": [
            {
                "name": "frameworks/base",
                "path": "frameworks/base",
                "enabled": True,
                "status": "available",
            }
        ],
        "tasks": [
            {
                "repository": "frameworks/base",
                "repository_path": "frameworks/base",
                "language": "java",
                "capability": "symbols",
                "parser": "java_symbol_importer",
                "status": "scheduled",
                "files": 1,
                "quality": "tags_only",
                "expected_evidence": ["node_type_prefix:JAVA_"],
            },
            {
                "repository": "frameworks/base",
                "repository_path": "frameworks/base",
                "language": "kotlin",
                "capability": "inheritance",
                "parser": None,
                "status": "unsupported",
                "files": 1,
                "quality": None,
                "expected_evidence": [],
            },
        ],
    }


def test_scheduled_parser_without_runtime_evidence_is_degraded(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    _database(database)

    report = evaluate_runtime_coverage(_plan(), database)

    java = report[0]
    assert java["planned_status"] == "scheduled"
    assert java["status"] == "degraded"
    assert java["observed_count"] == 0
    assert java["degradation_reasons"] == ["required_evidence_not_observed"]
    assert report[1]["status"] == "unsupported"


def test_observed_evidence_preserves_declared_quality(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO node (node_id, node_type, source_path)
            VALUES ('JAVA_CLASS:demo.A', 'JAVA_CLASS',
                    'frameworks/base/core/java/demo/A.java')
            """
        )

    report = evaluate_runtime_coverage(_plan(), database)

    assert report[0]["status"] == "supported"
    assert report[0]["quality"] == "tags_only"
    assert report[0]["observed_count"] == 1
    assert report[0]["degradation_reasons"] == []


def test_strict_gate_rejects_zero_required_evidence(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    _database(database)
    report_path = tmp_path / "capability-report.json"

    with pytest.raises(RuntimeCoverageError, match="runtime coverage gaps"):
        validate_runtime_coverage(_plan(strict=True), database, report_path)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload[0]["status"] == "degraded"
    assert payload[0]["observed_count"] == 0


def test_evidence_from_another_language_does_not_satisfy_task(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO edge (edge_id, edge_type, source_path)
            VALUES ('registration', 'REGISTERED_AS',
                    'frameworks/base/services/demo/JavaService.java')
            """
        )
    plan = _plan()
    plan["tasks"] = [
        {
            "repository": "frameworks/base",
            "repository_path": "frameworks/base",
            "language": "kotlin",
            "capability": "service_registration",
            "parser": "kotlin_service_importer",
            "status": "scheduled",
            "files": 1,
            "quality": "heuristic",
            "expected_evidence": ["edge_type:REGISTERED_AS"],
        }
    ]

    report = evaluate_runtime_coverage(plan, database)

    assert report[0]["status"] == "degraded"
    assert report[0]["observed_count"] == 0


def test_typed_table_evidence_is_scoped_by_repository_and_language(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE call_site(
              call_site_id TEXT PRIMARY KEY,
              repository TEXT,
              source_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO call_site VALUES (
              'CALL_SITE:1',
              'frameworks/base',
              'frameworks/base/packages/SystemUI/src/demo/Example.kt'
            )
            """
        )
    plan = _plan()
    plan["tasks"] = [
        {
            "repository": "frameworks/base",
            "repository_path": "frameworks/base",
            "language": "kotlin",
            "capability": "call_graph",
            "parser": "codeql_java_kotlin_importer",
            "status": "scheduled",
            "files": 1,
            "quality": "semantic",
            "expected_evidence": ["typed_table:call_site"],
        }
    ]

    report = evaluate_runtime_coverage(plan, database)

    assert report[0]["status"] == "supported"
    assert report[0]["observed_count"] == 1
