from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from workspace.multi_vendor import (
    VendorImportError,
    ensure_staged_database,
    import_vendor_artifacts,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_live_database_target_is_forbidden(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    live_database = data_root / "android_context.db"
    live_database.parent.mkdir()
    sqlite3.connect(live_database).close()

    with pytest.raises(VendorImportError, match="staged database"):
        ensure_staged_database(live_database, data_root / "staging/build-1")


def test_exact_staged_database_target_is_accepted(tmp_path: Path) -> None:
    staging = tmp_path / "data/staging/build-1"
    database = staging / "android_context.db"
    staging.mkdir(parents=True)
    sqlite3.connect(database).close()

    ensure_staged_database(database, staging)


def test_imported_vendor_definitions_trace_to_artifact_manifest(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "data/staging/build-1"
    database = staging / "android_context.db"
    source_dir = tmp_path / "cache/cache-key/sources"
    source = source_dir / "vendor/demo/VendorService.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """package vendor.demo;
class VendorBase {}
class VendorService extends VendorBase {}
""",
        encoding="utf-8",
    )
    staging.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.executescript(
        (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
    )
    connection.close()
    artifact_sha = "a" * 64
    manifest = tmp_path / "vendor-artifacts.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "artifacts": [
                    {
                        "artifact_name": "services.jar",
                        "artifact_path": str(tmp_path / "vendor-input/services.jar"),
                        "artifact_sha256": artifact_sha,
                        "artifact_size": 123,
                        "cache_key": "cache-key",
                        "status": "prepared",
                        "exit_status": 0,
                        "output_sha256": "b" * 64,
                        "source_file_count": 1,
                        "source_dir": str(source_dir),
                        "jadx": {
                            "path": "/tools/jadx",
                            "version": "jadx 1.5.6",
                        },
                        "options": ["--no-res"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = import_vendor_artifacts(
        manifest,
        database,
        staging,
        staging / "raw/vendor",
    )

    assert report["summary"]["artifacts_imported"] == 1
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    artifact = connection.execute(
        "SELECT properties_json FROM node WHERE node_type='VENDOR_ARTIFACT'"
    ).fetchone()
    traced = connection.execute(
        """
        SELECT COUNT(*)
        FROM edge
        WHERE edge_type='DERIVED_FROM_ARTIFACT'
          AND to_node_id=?
        """,
        (f"VENDOR_ARTIFACT:{artifact_sha}",),
    ).fetchone()[0]
    vendor_revision = connection.execute(
        """
        SELECT source_revision
        FROM node
        WHERE node_type='SYMBOL_DEFINITION'
          AND source_revision=?
        LIMIT 1
        """,
        (artifact_sha,),
    ).fetchone()
    inheritance = connection.execute(
        """
        SELECT properties_json, source_revision
        FROM edge
        WHERE edge_type='EXTENDS'
          AND source_revision=?
        """,
        (artifact_sha,),
    ).fetchone()
    connection.close()

    assert json.loads(artifact["properties_json"])["manifest_sha256"]
    assert traced >= 2
    assert vendor_revision is not None
    assert json.loads(inheritance["properties_json"])["artifact_sha256"] == artifact_sha
