from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from workspace.codeql_database import (
    CodeQLDatabaseManifest,
    RepositoryIdentity,
    _database_content_fingerprint,
    manifest_preparation_fingerprint,
)
from workspace.codeql_import import (
    CodeQLImportError,
    _validate_database,
    _validate_query_tool_identity,
)
from workspace.codeql_runner import QueryRunManifest


def repository(path: str, digit: str) -> RepositoryIdentity:
    return RepositoryIdentity(
        name=path,
        path=path,
        revision=digit * 40,
        dirty=False,
        inventory_sha256=digit * 64,
        file_count=10,
    )


def fixture(
    tmp_path: Path,
    *,
    planned: tuple[RepositoryIdentity, ...],
    observed: tuple[RepositoryIdentity, ...],
) -> tuple[Path, Path]:
    source_fingerprint = hashlib.sha256(
        json.dumps(
            [asdict(item) for item in sorted(observed, key=lambda item: item.path)],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest = CodeQLDatabaseManifest(
        schema_version=3,
        status="verified",
        cache_key="0" * 64,
        language="java-kotlin",
        source_fingerprint=source_fingerprint,
        product="aosp_cf_x86_64_phone",
        variant="userdebug",
        build_targets=("services", "SystemUI"),
        threads=8,
        ram_mb=24576,
        codeql_version="2.26.3",
        extractor_version="java-kotlin:fixture",
        database_fingerprint="0" * 64,
        database_marker_sha256="0" * 64,
        database_content_sha256="0" * 64,
        database_content_file_count=1,
        database_content_bytes=1,
        observed_java_files=10,
        observed_kotlin_files=2,
        repositories=observed,
        created_at="2026-08-20T00:00:00+00:00",
        database_info={"languages": ["java-kotlin"]},
    )
    cache_key = manifest_preparation_fingerprint(manifest)
    entry = tmp_path / cache_key
    database = entry / "database"
    database.mkdir(parents=True)
    marker = database / "codeql-database.yml"
    marker.write_text("primaryLanguage: java-kotlin\n", encoding="utf-8")
    marker_digest = hashlib.sha256(marker.read_bytes()).hexdigest()
    content_digest, content_file_count, content_bytes = (
        _database_content_fingerprint(database)
    )
    database_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "cache_key": cache_key,
                "database_info": manifest.database_info,
                "database_marker_sha256": marker_digest,
                "database_content_sha256": content_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest = replace(
        manifest,
        cache_key=cache_key,
        database_marker_sha256=marker_digest,
        database_content_sha256=content_digest,
        database_content_file_count=content_file_count,
        database_content_bytes=content_bytes,
        database_fingerprint=database_fingerprint,
    )
    (entry / "manifest.json").write_text(
        json.dumps(manifest.to_dict()),
        encoding="utf-8",
    )
    plan = tmp_path / "execution-plan.json"
    plan.write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "name": item.name,
                        "path": item.path,
                        "enabled": True,
                        "status": "available",
                        "revision": item.revision,
                        "revision_dirty": item.dirty,
                        "inventory_sha256": item.inventory_sha256,
                        "inventory_file_count": item.file_count,
                    }
                    for item in planned
                ]
            }
        ),
        encoding="utf-8",
    )
    return database, plan


@pytest.mark.parametrize("direction", ["missing", "extra"])
def test_database_validation_requires_exact_repository_identity_set(
    tmp_path: Path,
    direction: str,
) -> None:
    base = repository("frameworks/base", "1")
    systemui = repository("frameworks/libs/systemui", "2")
    if direction == "missing":
        database, plan = fixture(
            tmp_path,
            planned=(base, systemui),
            observed=(base,),
        )
    else:
        database, plan = fixture(
            tmp_path,
            planned=(base,),
            observed=(base, systemui),
        )

    with pytest.raises(CodeQLImportError, match="repository set mismatch"):
        _validate_database(database, plan)


def test_database_validation_accepts_exact_repository_identities(
    tmp_path: Path,
) -> None:
    base = repository("frameworks/base", "1")
    database, plan = fixture(tmp_path, planned=(base,), observed=(base,))

    manifest = _validate_database(database, plan)

    assert manifest.repositories == (base,)


def test_query_tool_identity_must_match_database_manifest(tmp_path: Path) -> None:
    base = repository("frameworks/base", "1")
    database, plan = fixture(tmp_path, planned=(base,), observed=(base,))
    manifest = _validate_database(database, plan)
    query_manifest = QueryRunManifest(
        schema_version=1,
        database_fingerprint=manifest.database_fingerprint,
        pack_lock_sha256="f" * 64,
        semantic_bundle_sha256="a" * 64,
        codeql_version=manifest.codeql_version,
        extractor_version="resolve-languages:" + "e" * 64,
        created_at="2026-08-20T00:00:00+00:00",
        queries=(),
    )

    with pytest.raises(CodeQLImportError, match="tool identity mismatch"):
        _validate_query_tool_identity(manifest, query_manifest)

    _validate_query_tool_identity(
        manifest,
        replace(query_manifest, extractor_version=manifest.extractor_version),
    )
