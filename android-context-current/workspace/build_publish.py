from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from graph.writer import GraphWriter, Node


RAW_REPORT_DIRECTORIES = ("ctags", "aidl", "inheritance", "service")


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildBatch:
    data_root: Path
    build_id: str
    staging_root: Path
    database: Path
    workspace: Path
    raw: Path
    rollback_root: Path
    journal: Path


def generate_build_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{os.getpid()}-{secrets.token_hex(4)}"


def _validate_build_id(build_id: str) -> str:
    if (
        not build_id
        or build_id in {".", ".."}
        or ".." in build_id
        or "/" in build_id
        or "\\" in build_id
    ):
        raise ValueError(f"unsafe build ID: {build_id!r}")
    return build_id


def _batch_from_parts(data_root: Path, build_id: str) -> BuildBatch:
    staging_root = data_root / "staging" / build_id
    return BuildBatch(
        data_root=data_root,
        build_id=build_id,
        staging_root=staging_root,
        database=staging_root / "android_context.db",
        workspace=staging_root / "workspace",
        raw=staging_root / "raw",
        rollback_root=data_root / "rollback" / build_id,
        journal=data_root / ".publish-journal.json",
    )


def begin_build(data_root: Path, build_id: str | None = None) -> BuildBatch:
    resolved_root = data_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    identity = _validate_build_id(build_id or generate_build_id())
    batch = _batch_from_parts(resolved_root, identity)
    batch.staging_root.mkdir(parents=True, exist_ok=False)
    batch.workspace.mkdir()
    batch.raw.mkdir()
    for name in RAW_REPORT_DIRECTORIES:
        (batch.raw / name).mkdir()
    return batch


def load_build_batch(staging_root: Path) -> BuildBatch:
    resolved_staging = staging_root.resolve()
    if resolved_staging.parent.name != "staging":
        raise ValueError(f"staging path must be under data/staging: {staging_root}")
    build_id = _validate_build_id(resolved_staging.name)
    return _batch_from_parts(resolved_staging.parent.parent, build_id)


def cleanup_failed_build(batch: BuildBatch, keep: bool) -> Path | None:
    if keep:
        return batch.staging_root.resolve() if batch.staging_root.exists() else None
    if batch.staging_root.exists():
        shutil.rmtree(batch.staging_root)
    return None


def record_graph_build(
    batch: BuildBatch,
    source_config: Path,
    started_at: str,
    verified_at: str,
) -> None:
    writer = GraphWriter(batch.database)
    try:
        writer.upsert_node(
            Node(
                node_id=f"GRAPH_BUILD:{batch.build_id}",
                node_type="GRAPH_BUILD",
                qualified_name=batch.build_id,
                display_name=batch.build_id,
                properties={
                    "source_config": str(source_config.resolve()),
                    "started_at": started_at,
                    "verified_at": verified_at,
                },
                extractor="build_publish",
            )
        )
    finally:
        writer.close()


def write_build_manifest(
    batch: BuildBatch,
    source_config: Path,
    started_at: str,
    verified_at: str,
) -> Path:
    manifest = batch.workspace / "build-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "build_id": batch.build_id,
                "source_config": str(source_config.resolve()),
                "started_at": started_at,
                "status": "verified",
                "verified_at": verified_at,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def read_graph_build_id(database: Path) -> str | None:
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(database)
        row = connection.execute(
            """
            SELECT qualified_name
            FROM node
            WHERE node_type = 'GRAPH_BUILD'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if "connection" in locals():
            connection.close()
    return str(row[0]) if row and row[0] is not None else None


def _database_sidecars(database: Path) -> tuple[Path, Path]:
    return Path(f"{database}-wal"), Path(f"{database}-shm")


def _assert_no_sidecars(database: Path) -> None:
    existing = [str(path) for path in _database_sidecars(database) if path.exists()]
    if existing:
        raise PublicationError(
            "SQLite sidecar files prevent publication: " + ", ".join(existing)
        )


def prepare_staged_database(database: Path) -> None:
    if not database.is_file():
        raise PublicationError(f"staged database does not exist: {database}")
    connection = sqlite3.connect(database, timeout=0.2)
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise PublicationError(f"staged database WAL is busy: {checkpoint}")
        mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if not mode or str(mode[0]).lower() != "delete":
            raise PublicationError(f"failed to disable staged WAL: {mode}")
    finally:
        connection.close()
    _assert_no_sidecars(database)


def ensure_live_database_quiescent(database: Path) -> None:
    if not database.exists():
        return
    try:
        connection = sqlite3.connect(database, timeout=0.2)
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise PublicationError(f"live database WAL is busy: {checkpoint}")
    except sqlite3.Error as error:
        raise PublicationError(f"cannot checkpoint live database: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()
    _assert_no_sidecars(database)
