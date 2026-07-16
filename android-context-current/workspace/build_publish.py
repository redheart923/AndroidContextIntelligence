from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


RAW_REPORT_DIRECTORIES = ("ctags", "aidl", "inheritance", "service")


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
