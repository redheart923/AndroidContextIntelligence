from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from graph.writer import GraphWriter, Node
from workspace.build_publish import (
    PublicationError,
    begin_build,
    cleanup_failed_build,
    ensure_live_database_quiescent,
    load_build_batch,
    main,
    prepare_staged_database,
    publish_build,
    read_graph_build_id,
    record_graph_build,
    recover_publication,
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


def seed_database(path: Path, build_id: str) -> None:
    create_full_node_schema(path)
    writer = GraphWriter(path)
    writer.upsert_node(
        Node(
            node_id=f"GRAPH_BUILD:{build_id}",
            node_type="GRAPH_BUILD",
            qualified_name=build_id,
            display_name=build_id,
            extractor="test",
        )
    )
    writer.close()


def seed_reports(root: Path, marker: str) -> None:
    for name in ("workspace", "raw"):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "marker.txt").write_text(marker, encoding="utf-8")


def ready_batch(data_root: Path, build_id: str = "new"):
    batch = begin_build(data_root, build_id=build_id)
    seed_database(batch.database, build_id)
    (batch.workspace / "marker.txt").write_text("new", encoding="utf-8")
    (batch.workspace / "build-manifest.json").write_text(
        json.dumps({"build_id": build_id, "status": "verified"}),
        encoding="utf-8",
    )
    (batch.raw / "marker.txt").write_text("new", encoding="utf-8")
    return batch


def test_publish_replaces_reports_and_database_as_one_batch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)

    publish_build(batch)

    assert read_graph_build_id(data / "android_context.db") == "new"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "new"
    assert (data / "raw/marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.journal.exists()
    assert not batch.rollback_root.exists()
    assert not batch.staging_root.exists()


def test_precommit_failure_restores_old_batch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    before = hashlib.sha256((data / "android_context.db").read_bytes()).hexdigest()
    batch = ready_batch(data)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected database replacement failure")

    with pytest.raises(OSError, match="injected"):
        publish_build(batch, replace_database=fail_replace)

    after = hashlib.sha256((data / "android_context.db").read_bytes()).hexdigest()
    assert after == before
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "old"
    assert (batch.workspace / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.journal.exists()


def test_publish_rejects_mismatched_identity_before_moving_live_files(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    (batch.workspace / "build-manifest.json").write_text(
        json.dumps({"build_id": "different", "status": "verified"}),
        encoding="utf-8",
    )

    with pytest.raises(PublicationError, match="build identity"):
        publish_build(batch)

    assert read_graph_build_id(data / "android_context.db") == "old"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "old"


def simulate_reports_published(batch) -> None:
    batch.rollback_root.mkdir(parents=True)
    os.replace(batch.data_root / "workspace", batch.rollback_root / "workspace")
    os.replace(batch.data_root / "raw", batch.rollback_root / "raw")
    os.replace(batch.workspace, batch.data_root / "workspace")
    os.replace(batch.raw, batch.data_root / "raw")
    batch.journal.write_text(
        json.dumps(
            {
                "build_id": batch.build_id,
                "staging_root": str(batch.staging_root),
                "rollback_root": str(batch.rollback_root),
                "phase": "new_reports_published",
            }
        ),
        encoding="utf-8",
    )


def test_recovery_rolls_back_when_database_has_old_build_id(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    simulate_reports_published(batch)

    assert recover_publication(data) == "rolled_back"
    assert read_graph_build_id(data / "android_context.db") == "old"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "old"
    assert (batch.workspace / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.journal.exists()


def test_recovery_finishes_cleanup_when_database_has_new_build_id(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    simulate_reports_published(batch)
    os.replace(batch.database, data / "android_context.db")

    assert recover_publication(data) == "committed"
    assert read_graph_build_id(data / "android_context.db") == "new"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.rollback_root.exists()
    assert not batch.journal.exists()
    assert recover_publication(data) == "no_journal"


def test_first_build_failure_leaves_live_database_absent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    batch = ready_batch(data)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected first-build failure")

    with pytest.raises(OSError, match="first-build"):
        publish_build(batch, replace_database=fail_replace)

    assert not (data / "android_context.db").exists()
    assert batch.database.exists()


def test_cli_begin_prints_only_staging_path(tmp_path: Path, capsys) -> None:
    assert main(["begin", "--data-root", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    staging = Path(captured.out.strip())
    assert captured.err == ""
    assert staging.parent == tmp_path.resolve() / "staging"
    assert staging.is_dir()


def test_cli_fail_keep_prints_retained_path(tmp_path: Path, capsys) -> None:
    batch = begin_build(tmp_path, build_id="build-1")

    assert main(["fail", "--staging", str(batch.staging_root), "--keep"]) == 0

    assert Path(capsys.readouterr().out.strip()) == batch.staging_root.resolve()


def test_cli_recover_without_journal_is_success(tmp_path: Path, capsys) -> None:
    assert main(["recover", "--data-root", str(tmp_path)]) == 0

    assert capsys.readouterr().out.strip() == "no_journal"


def test_cli_prepare_records_and_checkpoints_batch(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    create_full_node_schema(batch.database)
    source_config = tmp_path / "source_roots.toml"
    source_config.write_text("[workspace]\n", encoding="utf-8")

    assert main(
        [
            "prepare",
            "--staging",
            str(batch.staging_root),
            "--source-config",
            str(source_config),
            "--started-at",
            "2026-07-16T15:00:00Z",
            "--verified-at",
            "2026-07-16T15:01:00Z",
        ]
    ) == 0

    assert read_graph_build_id(batch.database) == "build-1"
    manifest = json.loads(
        (batch.workspace / "build-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["build_id"] == "build-1"


def test_cli_publish_commits_ready_batch(tmp_path: Path) -> None:
    batch = ready_batch(tmp_path, build_id="build-1")

    assert main(["publish", "--staging", str(batch.staging_root)]) == 0

    assert read_graph_build_id(tmp_path / "android_context.db") == "build-1"
