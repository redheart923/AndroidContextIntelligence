from __future__ import annotations

from pathlib import Path

import pytest

from workspace.build_publish import (
    begin_build,
    cleanup_failed_build,
    load_build_batch,
)


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
