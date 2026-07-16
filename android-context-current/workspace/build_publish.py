from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_publication_journal(batch: BuildBatch, phase: str) -> None:
    payload = {
        "build_id": batch.build_id,
        "staging_root": str(batch.staging_root),
        "rollback_root": str(batch.rollback_root),
        "phase": phase,
    }
    batch.journal.parent.mkdir(parents=True, exist_ok=True)
    temporary = batch.journal.with_name(
        f"{batch.journal.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, batch.journal)
        _fsync_directory(batch.journal.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _read_verified_manifest_build_id(batch: BuildBatch) -> str | None:
    manifest = batch.workspace / "build-manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("status") != "verified":
        return None
    identity = payload.get("build_id")
    return str(identity) if identity is not None else None


def _validate_batch_identity(batch: BuildBatch) -> None:
    database_build_id = read_graph_build_id(batch.database)
    manifest_build_id = _read_verified_manifest_build_id(batch)
    if database_build_id != batch.build_id or manifest_build_id != batch.build_id:
        raise PublicationError(
            "build identity mismatch: "
            f"expected {batch.build_id!r}, database={database_build_id!r}, "
            f"manifest={manifest_build_id!r}"
        )


def _restore_precommit_reports(
    batch: BuildBatch,
    published_names: set[str],
    backed_up_names: set[str],
) -> None:
    batch.staging_root.mkdir(parents=True, exist_ok=True)
    for name in published_names:
        live = batch.data_root / name
        staged = batch.staging_root / name
        if live.exists():
            if staged.exists():
                raise PublicationError(
                    f"cannot restore staged {name}: destination already exists"
                )
            os.replace(live, staged)
    for name in backed_up_names:
        backup = batch.rollback_root / name
        live = batch.data_root / name
        if backup.exists():
            if live.exists():
                raise PublicationError(
                    f"cannot restore live {name}: destination already exists"
                )
            os.replace(backup, live)
    if batch.rollback_root.exists():
        shutil.rmtree(batch.rollback_root)


def publish_build(
    batch: BuildBatch,
    *,
    replace_database: Callable[[Path, Path], None] = os.replace,
) -> None:
    _validate_batch_identity(batch)
    _assert_no_sidecars(batch.database)
    ensure_live_database_quiescent(batch.data_root / "android_context.db")
    if batch.journal.exists():
        raise PublicationError(
            f"publication journal already exists: {batch.journal}; recover first"
        )

    backed_up_names: set[str] = set()
    published_names: set[str] = set()
    database_committed = False
    _write_publication_journal(batch, "prepared")
    try:
        batch.rollback_root.mkdir(parents=True, exist_ok=False)
        for name in ("workspace", "raw"):
            live = batch.data_root / name
            if live.exists():
                os.replace(live, batch.rollback_root / name)
                backed_up_names.add(name)
        _write_publication_journal(batch, "old_reports_backed_up")

        for name in ("workspace", "raw"):
            staged = batch.staging_root / name
            if not staged.is_dir():
                raise PublicationError(f"staged report directory is missing: {staged}")
            os.replace(staged, batch.data_root / name)
            published_names.add(name)
        _write_publication_journal(batch, "new_reports_published")

        replace_database(batch.database, batch.data_root / "android_context.db")
        database_committed = True
        _write_publication_journal(batch, "database_committed")
    except BaseException:
        if not database_committed:
            _restore_precommit_reports(batch, published_names, backed_up_names)
            if batch.journal.exists():
                batch.journal.unlink()
                _fsync_directory(batch.journal.parent)
        raise

    if batch.rollback_root.exists():
        shutil.rmtree(batch.rollback_root)
    if batch.staging_root.exists():
        shutil.rmtree(batch.staging_root)
    if batch.journal.exists():
        batch.journal.unlink()
        _fsync_directory(batch.journal.parent)


def _load_journal_batch(data_root: Path, payload: dict[str, object]) -> BuildBatch:
    identity = _validate_build_id(str(payload.get("build_id", "")))
    batch = _batch_from_parts(data_root, identity)
    try:
        staging = Path(str(payload["staging_root"])).resolve()
        rollback = Path(str(payload["rollback_root"])).resolve()
    except (KeyError, OSError) as error:
        raise PublicationError("invalid publication journal paths") from error
    if staging != batch.staging_root.resolve() or rollback != batch.rollback_root.resolve():
        raise PublicationError("publication journal paths do not match build identity")
    return batch


def recover_publication(data_root: Path) -> str:
    resolved_root = data_root.resolve()
    journal = resolved_root / ".publish-journal.json"
    if not journal.exists():
        return "no_journal"
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicationError(f"invalid publication journal: {journal}") from error
    if not isinstance(payload, dict):
        raise PublicationError(f"invalid publication journal: {journal}")
    batch = _load_journal_batch(resolved_root, payload)

    live_database = resolved_root / "android_context.db"
    if read_graph_build_id(live_database) == batch.build_id:
        if batch.rollback_root.exists():
            shutil.rmtree(batch.rollback_root)
        if batch.staging_root.exists():
            shutil.rmtree(batch.staging_root)
        journal.unlink()
        _fsync_directory(journal.parent)
        return "committed"

    batch.staging_root.mkdir(parents=True, exist_ok=True)
    for name in ("workspace", "raw"):
        live = resolved_root / name
        staged = batch.staging_root / name
        backup = batch.rollback_root / name
        if live.exists() and not staged.exists():
            os.replace(live, staged)
        if backup.exists():
            if live.exists():
                raise PublicationError(
                    f"cannot recover {name}: live and rollback paths both exist"
                )
            os.replace(backup, live)
    if batch.rollback_root.exists():
        shutil.rmtree(batch.rollback_root)
    journal.unlink()
    _fsync_directory(journal.parent)
    return "rolled_back"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish verified graph build batches")
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("begin", help="create a staged build batch")
    begin.add_argument("--data-root", type=Path, required=True)

    prepare = commands.add_parser("prepare", help="identify and prepare a batch")
    prepare.add_argument("--staging", type=Path, required=True)
    prepare.add_argument("--source-config", type=Path, required=True)
    prepare.add_argument("--started-at", required=True)
    prepare.add_argument("--verified-at", required=True)

    publish = commands.add_parser("publish", help="publish a verified batch")
    publish.add_argument("--staging", type=Path, required=True)

    fail = commands.add_parser("fail", help="clean up or retain a failed batch")
    fail.add_argument("--staging", type=Path, required=True)
    fail.add_argument("--keep", action="store_true")

    recover = commands.add_parser("recover", help="recover interrupted publication")
    recover.add_argument("--data-root", type=Path, required=True)
    return parser


def main(argument_vector: list[str] | None = None) -> int:
    arguments = _build_argument_parser().parse_args(argument_vector)
    if arguments.command == "begin":
        print(begin_build(arguments.data_root).staging_root.resolve())
        return 0
    if arguments.command == "prepare":
        batch = load_build_batch(arguments.staging)
        record_graph_build(
            batch,
            arguments.source_config,
            arguments.started_at,
            arguments.verified_at,
        )
        write_build_manifest(
            batch,
            arguments.source_config,
            arguments.started_at,
            arguments.verified_at,
        )
        prepare_staged_database(batch.database)
        return 0
    if arguments.command == "publish":
        publish_build(load_build_batch(arguments.staging))
        return 0
    if arguments.command == "fail":
        retained = cleanup_failed_build(
            load_build_batch(arguments.staging),
            keep=arguments.keep,
        )
        if retained is not None:
            print(retained)
        return 0
    if arguments.command == "recover":
        print(recover_publication(arguments.data_root))
        return 0
    raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
