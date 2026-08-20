from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from workspace.codeql_database import (
    CodeQLDatabaseError,
    CodeQLDatabaseManifest,
    PreparationRequest,
    RepositoryIdentity,
    build_codeql_create_command,
    build_script_text,
    preparation_fingerprint,
    validate_database_manifest,
    validate_manifest_self_consistency,
)


def fixture_request(
    tmp_path: Path,
    *,
    targets: tuple[str, ...] = ("services", "SystemUI"),
    revision: str = "abc",
) -> PreparationRequest:
    aosp = tmp_path / "aosp"
    aosp.mkdir(exist_ok=True)
    codeql = tmp_path / "codeql"
    codeql.write_text("fixture", encoding="utf-8")
    return PreparationRequest(
        aosp_root=aosp,
        codeql_bin=codeql,
        product="aosp_cf_x86_64_phone",
        variant="userdebug",
        build_targets=targets,
        threads=8,
        ram_mb=24576,
        cache_root=tmp_path / "cache",
        repositories=(
            RepositoryIdentity(
                name="frameworks/base",
                path="frameworks/base",
                revision=revision,
                dirty=False,
                inventory_sha256="1" * 64,
                file_count=42,
            ),
        ),
        codeql_version="2.23.1",
        extractor_version="java-kotlin:fixture",
    )


def fixture_manifest(request: PreparationRequest) -> CodeQLDatabaseManifest:
    cache_key = preparation_fingerprint(request)
    return CodeQLDatabaseManifest(
        schema_version=2,
        status="verified",
        cache_key=cache_key,
        language="java-kotlin",
        source_fingerprint=request.source_fingerprint,
        product=request.product,
        variant=request.variant,
        build_targets=request.build_targets,
        threads=request.threads,
        ram_mb=request.ram_mb,
        codeql_version=request.codeql_version,
        extractor_version=request.extractor_version,
        database_fingerprint="2" * 64,
        database_marker_sha256="3" * 64,
        observed_java_files=10,
        observed_kotlin_files=2,
        repositories=request.repositories,
        created_at="2026-08-20T00:00:00+00:00",
        database_info={"languages": ["java-kotlin"]},
    )


def test_preparation_fingerprint_changes_with_target_and_revision(
    tmp_path: Path,
) -> None:
    base = fixture_request(tmp_path)

    assert preparation_fingerprint(base) == preparation_fingerprint(base)
    assert preparation_fingerprint(base) != preparation_fingerprint(
        replace(base, build_targets=("services",))
    )
    assert preparation_fingerprint(base) != preparation_fingerprint(
        replace(
            base,
            repositories=(replace(base.repositories[0], revision="def"),),
        )
    )


def test_preparation_fingerprint_is_independent_of_repository_order(
    tmp_path: Path,
) -> None:
    base = fixture_request(tmp_path)
    second = RepositoryIdentity(
        name="packages/SystemUI",
        path="packages/SystemUI",
        revision="def",
        dirty=False,
        inventory_sha256="5" * 64,
        file_count=7,
    )
    ordered = replace(base, repositories=(base.repositories[0], second))
    reversed_order = replace(base, repositories=(second, base.repositories[0]))

    assert preparation_fingerprint(ordered) == preparation_fingerprint(
        reversed_order
    )


def test_build_command_uses_isolated_out_and_manual_java_kotlin_build(
    tmp_path: Path,
) -> None:
    request = fixture_request(tmp_path)
    cache_key = "a" * 64
    script = tmp_path / "trace-build.sh"

    command = build_codeql_create_command(
        request,
        cache_key=cache_key,
        database=tmp_path / "database",
        build_script=script,
    )
    traced = build_script_text(request, cache_key)

    assert "--language=java-kotlin" in command
    assert "--command" in command
    assert command[command.index("--command") + 1].endswith(
        "trace-build.sh"
    )
    assert str(request.cache_root / "build-out" / cache_key) in traced
    assert "source build/envsetup.sh" in traced
    assert "lunch aosp_cf_x86_64_phone-userdebug" in traced
    assert "m -j8 services SystemUI" in traced


def test_manifest_rejects_source_inventory_mismatch(tmp_path: Path) -> None:
    request = fixture_request(tmp_path)
    manifest = fixture_manifest(request)

    with pytest.raises(CodeQLDatabaseError, match="source_fingerprint"):
        validate_database_manifest(
            manifest,
            expected_request=replace(
                request,
                repositories=(
                    replace(
                        request.repositories[0],
                        inventory_sha256="4" * 64,
                    ),
                ),
            ),
        )


def test_manifest_json_round_trip_is_stable(tmp_path: Path) -> None:
    request = fixture_request(tmp_path)
    manifest = fixture_manifest(request)

    restored = CodeQLDatabaseManifest.from_dict(
        json.loads(json.dumps(manifest.to_dict(), sort_keys=True))
    )

    assert restored == manifest


def test_manifest_self_consistency_rejects_tampered_cache_key(
    tmp_path: Path,
) -> None:
    request = fixture_request(tmp_path)
    manifest = fixture_manifest(request)
    database = tmp_path / manifest.cache_key / "database"
    database.mkdir(parents=True)
    marker = database / "codeql-database.yml"
    marker.write_text("primaryLanguage: java-kotlin\n", encoding="utf-8")
    marker_digest = hashlib.sha256(marker.read_bytes()).hexdigest()
    manifest = replace(
        manifest,
        database_marker_sha256=marker_digest,
        database_fingerprint=hashlib.sha256(
            json.dumps(
                {
                    "cache_key": manifest.cache_key,
                    "database_info": manifest.database_info,
                    "database_marker_sha256": marker_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    )

    with pytest.raises(CodeQLDatabaseError, match="cache_key"):
        validate_manifest_self_consistency(
            replace(manifest, cache_key="4" * 64),
            database=database,
        )


def test_manifest_self_consistency_rejects_tampered_source_fingerprint(
    tmp_path: Path,
) -> None:
    request = fixture_request(tmp_path)
    manifest = fixture_manifest(request)
    database = tmp_path / manifest.cache_key / "database"
    database.mkdir(parents=True)

    with pytest.raises(CodeQLDatabaseError, match="source_fingerprint"):
        validate_manifest_self_consistency(
            replace(manifest, source_fingerprint="4" * 64),
            database=database,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product", ""),
        ("variant", ""),
        ("build_targets", ()),
        ("codeql_version", ""),
        ("extractor_version", ""),
        ("repositories", ()),
    ],
)
def test_request_rejects_incomplete_build_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    request = fixture_request(tmp_path)

    with pytest.raises(CodeQLDatabaseError):
        replace(request, **{field: value})
