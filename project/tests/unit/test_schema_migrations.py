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

    assert first == ("0001_call_dataflow",)
    assert second == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
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
