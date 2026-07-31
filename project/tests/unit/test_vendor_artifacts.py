from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from workspace.vendor_artifacts import (
    VendorPreparationError,
    prepare_artifacts,
)


def write_fake_jadx(
    path: Path,
    *,
    version: str,
    exit_status: int = 0,
    emit_source: bool = True,
) -> None:
    source_statement = (
        "(output / 'sources/demo/VendorService.java').parent.mkdir("
        "parents=True, exist_ok=True)\n"
        "(output / 'sources/demo/VendorService.java').write_text("
        "'package demo; class VendorService {}\\n', encoding='utf-8')"
        if emit_source
        else "pass"
    )
    path.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import sys
if '--version' in sys.argv:
    print({version!r})
    raise SystemExit(0)
output = pathlib.Path(sys.argv[sys.argv.index('-d') + 1])
{source_statement}
log = pathlib.Path(__file__).with_suffix('.log')
with log.open('a', encoding='utf-8') as stream:
    stream.write('run\\n')
raise SystemExit({exit_status})
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_content_addressed_cache_tracks_artifact_and_jadx_identity(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "vendor-input"
    cache_dir = tmp_path / "cache"
    input_dir.mkdir()
    artifact = input_dir / "services.jar"
    artifact.write_bytes(b"first artifact")
    jadx = tmp_path / "jadx"
    write_fake_jadx(jadx, version="jadx 1.5.6")

    first = prepare_artifacts(input_dir, cache_dir, jadx)
    second = prepare_artifacts(input_dir, cache_dir, jadx)

    assert first["artifacts"][0]["status"] == "prepared"
    assert first["artifacts"][0]["artifact_sha256"] == hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()
    assert first["artifacts"][0]["jadx"]["version"] == "jadx 1.5.6"
    assert first["artifacts"][0]["output_sha256"]
    assert first["artifacts"][0]["source_file_count"] == 1
    assert second["artifacts"][0]["cache_status"] == "reused"
    assert jadx.with_suffix(".log").read_text(encoding="utf-8") == "run\n"

    cache_manifest = (
        cache_dir / first["artifacts"][0]["cache_key"] / "manifest.json"
    )
    tampered = json.loads(cache_manifest.read_text(encoding="utf-8"))
    tampered["jadx"]["version"] = "tampered"
    cache_manifest.write_text(json.dumps(tampered), encoding="utf-8")
    repaired = prepare_artifacts(input_dir, cache_dir, jadx)
    assert repaired["artifacts"][0]["cache_status"] == "created"
    assert jadx.with_suffix(".log").read_text(encoding="utf-8") == "run\nrun\n"

    copied_input = tmp_path / "copied-input"
    copied_input.mkdir()
    copied = copied_input / "renamed-services.jar"
    copied.write_bytes(artifact.read_bytes())
    copied_report = prepare_artifacts(copied_input, cache_dir, jadx)
    assert copied_report["artifacts"][0]["cache_status"] == "reused"
    assert copied_report["artifacts"][0]["artifact_name"] == copied.name
    assert copied_report["artifacts"][0]["artifact_path"] == str(copied.resolve())

    artifact.write_bytes(b"changed artifact")
    changed_artifact = prepare_artifacts(input_dir, cache_dir, jadx)
    assert (
        changed_artifact["artifacts"][0]["cache_key"]
        != first["artifacts"][0]["cache_key"]
    )

    jadx_v2 = tmp_path / "jadx-v2"
    write_fake_jadx(jadx_v2, version="jadx 1.5.7")
    changed_tool = prepare_artifacts(input_dir, cache_dir, jadx_v2)
    assert (
        changed_tool["artifacts"][0]["cache_key"]
        != changed_artifact["artifacts"][0]["cache_key"]
    )


def test_partial_decompilation_is_explicitly_degraded(tmp_path: Path) -> None:
    input_dir = tmp_path / "vendor-input"
    input_dir.mkdir()
    (input_dir / "partial.apk").write_bytes(b"partial")
    jadx = tmp_path / "jadx"
    write_fake_jadx(
        jadx,
        version="jadx 1.5.6",
        exit_status=1,
        emit_source=True,
    )

    report = prepare_artifacts(input_dir, tmp_path / "cache", jadx)

    artifact = report["artifacts"][0]
    assert artifact["status"] == "degraded"
    assert artifact["exit_status"] == 1
    assert artifact["source_file_count"] == 1
    assert report["summary"] == {
        "prepared": 0,
        "reused": 0,
        "degraded": 1,
        "failed": 0,
    }

    reused = prepare_artifacts(input_dir, tmp_path / "cache", jadx)
    assert reused["artifacts"][0]["status"] == "degraded"
    assert reused["summary"]["reused"] == 1
    assert reused["summary"]["degraded"] == 1


def test_failed_decompilation_has_no_usable_cache_entry(tmp_path: Path) -> None:
    input_dir = tmp_path / "vendor-input"
    input_dir.mkdir()
    (input_dir / "broken.apk").write_bytes(b"broken")
    jadx = tmp_path / "jadx"
    write_fake_jadx(
        jadx,
        version="jadx 1.5.6",
        exit_status=2,
        emit_source=False,
    )

    report = prepare_artifacts(input_dir, tmp_path / "cache", jadx)

    artifact = report["artifacts"][0]
    assert artifact["status"] == "failed"
    assert artifact["source_dir"] is None
    assert report["summary"]["failed"] == 1


def test_vendor_input_inside_data_root_is_rejected(tmp_path: Path) -> None:
    input_dir = tmp_path / "data/raw/vendor"
    input_dir.mkdir(parents=True)
    jadx = tmp_path / "jadx"
    write_fake_jadx(jadx, version="jadx 1.5.6")

    with pytest.raises(VendorPreparationError, match="outside graph data"):
        prepare_artifacts(
            input_dir,
            tmp_path / "cache",
            jadx,
            data_root=tmp_path / "data",
        )
