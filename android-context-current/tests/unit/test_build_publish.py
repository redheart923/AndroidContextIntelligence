from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from workspace.build_publish import (
    PublicationError,
    begin_build,
    cleanup_failed_build,
    ensure_live_database_quiescent,
    load_build_batch,
    prepare_staged_database,
    read_graph_build_id,
    record_graph_build,
    write_build_manifest,
)


def create_full_node_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE node (
          node_id TEXT PRIMARY KEY,
          node_type TEXT NOT NULL,
          qualified_name TEXT,
          display_name TEXT NOT NULL,
          properties_json TEXT NOT NULL DEFAULT '{}',
          source_path TEXT,
          line_start INTEGER,
          line_end INTEGER,
          source_revision TEXT,
          extractor TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          content_hash TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          updated_at TEXT NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()


def test_begin_build_creates_isolated_batch(tmp_path: Path) -> None:
    live_db = tmp_path / "android_context.db"
    live_db.write_bytes(b"verified")

    batch = begin_build(tmp_path, build_id="build-1")

    assert batch.staging_root == tmp_path / "staging" / "build-1"
    assert batch.database == batch.staging_root / "android_context.db"
    assert batch.workspace.is_dir()
    assert batch.raw.is_dir()
    assert (batch.raw / "ctags").is_dir()
    assert (batch.raw / "aidl").is_dir()
    assert (batch.raw / "inheritance").is_dir()
    assert (batch.raw / "service").is_dir()
    assert live_db.read_bytes() == b"verified"


def test_load_build_batch_reconstructs_paths(tmp_path: Path) -> None:
    created = begin_build(tmp_path, build_id="build-1")

    loaded = load_build_batch(created.staging_root)

    assert loaded == created


@pytest.mark.parametrize("build_id", ["../escape", "nested/name", "nested\\name", ".."])
def test_begin_build_rejects_unsafe_build_id(tmp_path: Path, build_id: str) -> None:
    with pytest.raises(ValueError, match="build ID"):
        begin_build(tmp_path, build_id=build_id)


def test_failed_build_is_deleted_by_default_and_cleanup_is_idempotent(
    tmp_path: Path,
) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    batch.database.write_bytes(b"partial")

    retained = cleanup_failed_build(batch, keep=False)

    assert retained is None
    assert not batch.staging_root.exists()
    assert cleanup_failed_build(batch, keep=False) is None


def test_keep_failed_build_preserves_complete_batch(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    batch.database.write_bytes(b"partial")
    (batch.workspace / "report.json").write_text("{}", encoding="utf-8")

    retained = cleanup_failed_build(batch, keep=True)

    assert retained == batch.staging_root.resolve()
    assert batch.database.read_bytes() == b"partial"
    assert (batch.workspace / "report.json").read_text(encoding="utf-8") == "{}"


def test_records_matching_database_and_manifest_build_ids(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    source_config = tmp_path / "source_roots.toml"
    source_config.write_text("[workspace]\n", encoding="utf-8")
    create_full_node_schema(batch.database)

    record_graph_build(
        batch,
        source_config,
        "2026-07-16T15:00:00Z",
        "2026-07-16T15:01:00Z",
    )
    write_build_manifest(
        batch,
        source_config,
        "2026-07-16T15:00:00Z",
        "2026-07-16T15:01:00Z",
    )

    assert read_graph_build_id(batch.database) == batch.build_id
    manifest = json.loads(
        (batch.workspace / "build-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "build_id": "build-1",
        "source_config": str(source_config.resolve()),
        "started_at": "2026-07-16T15:00:00Z",
        "status": "verified",
        "verified_at": "2026-07-16T15:01:00Z",
    }


def test_prepare_staged_database_removes_wal_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "staged.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sample(value TEXT)")
    connection.execute("INSERT INTO sample VALUES('value')")
    connection.commit()
    connection.close()

    prepare_staged_database(database)

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    connection.close()
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def test_busy_live_database_rejects_publication(tmp_path: Path) -> None:
    database = tmp_path / "android_context.db"
    active = sqlite3.connect(database)
    active.execute("PRAGMA journal_mode=WAL")
    active.execute("CREATE TABLE sample(value TEXT)")
    active.execute("INSERT INTO sample VALUES('active')")
    active.commit()
    sidecar = Path(f"{database}-wal")
    assert sidecar.exists()

    try:
        with pytest.raises(PublicationError, match="sidecar"):
            ensure_live_database_quiescent(database)
        assert sidecar.exists()
    finally:
        active.close()


def test_missing_live_database_is_quiescent(tmp_path: Path) -> None:
    ensure_live_database_quiescent(tmp_path / "missing.db")
