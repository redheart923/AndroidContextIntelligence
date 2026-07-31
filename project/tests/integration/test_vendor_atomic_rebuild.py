from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from workspace.build_publish import begin_build
from workspace.multi_vendor import VendorImportError, import_vendor_artifacts


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vendor_import_failure_leaves_live_database_unchanged(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    live_database = data_root / "android_context.db"
    live_database.write_bytes(b"verified-live-database")
    before = hashlib.sha256(live_database.read_bytes()).hexdigest()
    batch = begin_build(data_root, build_id="vendor-failure")
    connection = sqlite3.connect(batch.database)
    connection.executescript(
        (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
    )
    connection.close()
    manifest = batch.workspace / "vendor-artifacts.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "artifacts": [
                    {
                        "artifact_name": "broken.jar",
                        "artifact_sha256": "a" * 64,
                        "status": "prepared",
                        "source_dir": str(tmp_path / "missing-sources"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(VendorImportError, match="source directory is missing"):
        import_vendor_artifacts(
            manifest,
            batch.database,
            batch.staging_root,
            batch.raw / "vendor",
        )

    after = hashlib.sha256(live_database.read_bytes()).hexdigest()
    assert after == before
    assert batch.database.is_file()


def test_compatibility_wrapper_has_no_live_database_target() -> None:
    script = (PROJECT_ROOT / "scripts/import_vendor.sh").read_text(
        encoding="utf-8"
    )

    assert "data/android_context.db" not in script
    assert "workspace.multi_vendor" not in script
    assert "scripts/rebuild_all.sh" in script
    assert "--vendor-input" in script
