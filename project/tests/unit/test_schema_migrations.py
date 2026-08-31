from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workspace.schema_migrations import MigrationError, apply_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = PROJECT_ROOT / "storage/migrations"


def initialize_base_schema(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )


def test_call_dataflow_migration_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    initialize_base_schema(database)

    first = apply_migrations(database, MIGRATIONS)
    second = apply_migrations(database, MIGRATIONS)

    assert first == (
        "0001_call_dataflow",
        "0002_effective_fact_views",
        "0003_native_build_candidates",
        "0004_native_candidate_corrections",
    )
    assert second == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "extraction_run",
        "semantic_definition",
        "call_site",
        "call_target",
        "program_value",
        "dataflow_path",
        "dataflow_step",
        "security_trace",
        "security_trace_step",
        "extraction_evidence",
        "fact_correction",
        "correction_application",
    } <= names


def test_native_candidate_migration_excludes_candidates_from_effective_view(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    initialize_base_schema(database)
    apply_migrations(database, MIGRATIONS)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO node(
              node_id, node_type, display_name, extractor,
              extractor_version, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                "EXTRACTION_CANDIDATE:demo",
                "EXTRACTION_CANDIDATE",
                "demo",
                "fixture",
                "1",
                "2026-08-25T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO node(
              node_id, node_type, display_name, extractor,
              extractor_version, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                "CPP_FUNCTION:demo",
                "CPP_FUNCTION",
                "demo",
                "fixture",
                "1",
                "2026-08-25T00:00:00+00:00",
            ),
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM effective_node"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM effective_node "
            "WHERE node_type='EXTRACTION_CANDIDATE'"
        ).fetchone()[0] == 0
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert "idx_node_candidate_status" in indexes


def test_failed_migration_rolls_back_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    initialize_base_schema(database)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        "CREATE TABLE partial(value TEXT); INVALID SQL;",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="0001_broken"):
        apply_migrations(database, migrations)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='partial'"
        ).fetchone()[0] == 0


def test_changed_applied_migration_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    initialize_base_schema(database)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    migration = migrations / "0001_demo.sql"
    migration.write_text("CREATE TABLE demo(value TEXT);", encoding="utf-8")
    assert apply_migrations(database, migrations) == ("0001_demo",)
    migration.write_text(
        "CREATE TABLE demo(value TEXT, changed TEXT);",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="digest mismatch"):
        apply_migrations(database, migrations)
