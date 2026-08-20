from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from workspace.codeql_database import CodeQLDatabaseManifest, RepositoryIdentity
from workspace.codeql_import import CodeQLImportError, _validate_database


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
    entry = tmp_path / "entry"
    database = entry / "database"
    database.mkdir(parents=True)
    marker = database / "codeql-database.yml"
    marker.write_text("primaryLanguage: java-kotlin\n", encoding="utf-8")
    manifest = CodeQLDatabaseManifest(
        schema_version=1,
        status="verified",
        cache_key="a" * 64,
        language="java-kotlin",
        source_fingerprint="b" * 64,
        product="aosp_cf_x86_64_phone",
        variant="userdebug",
        build_targets=("services", "SystemUI"),
        codeql_version="2.26.3",
        extractor_version="java-kotlin:fixture",
        database_fingerprint="c" * 64,
        database_marker_sha256=hashlib.sha256(marker.read_bytes()).hexdigest(),
        observed_java_files=10,
        observed_kotlin_files=2,
        repositories=observed,
        created_at="2026-08-20T00:00:00+00:00",
        database_info={"languages": ["java-kotlin"]},
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
                        "inventory_sha256": item.inventory_sha256,
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
