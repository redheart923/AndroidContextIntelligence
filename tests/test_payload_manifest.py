from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import project_payload


def write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_write_manifest_is_stable_sorted_and_loadable(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    output = tmp_path / "install-manifest.json"
    write(payload, "workspace/zeta.py", "zeta\n")
    write(payload, "collectors/alpha.py", "alpha\n")

    project_payload.write_manifest(payload, output, "abc123")

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["source_commit"] == "abc123"
    assert list(document["files"]) == [
        "collectors/alpha.py",
        "workspace/zeta.py",
    ]
    assert output.read_bytes().endswith(b"\n")
    assert list(tmp_path.glob(".install-manifest.json.*.tmp")) == []

    loaded = project_payload.load_manifest(output)
    assert loaded.schema_version == 1
    assert loaded.source_commit == "abc123"
    assert loaded.files == document["files"]


def test_write_manifest_replaces_existing_file(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    output = tmp_path / "install-manifest.json"
    write(payload, "graph/writer.py", "first\n")
    output.write_text("old\n", encoding="utf-8")

    project_payload.write_manifest(payload, output, "replacement")

    assert project_payload.load_manifest(output).source_commit == "replacement"


@pytest.mark.parametrize(
    "document, message",
    [
        ({"schema_version": 2, "source_commit": "a", "files": {}}, "schema"),
        ({"schema_version": 1, "source_commit": "", "files": {}}, "source_commit"),
        ({"schema_version": 1, "source_commit": "a", "files": []}, "files"),
    ],
)
def test_load_manifest_rejects_invalid_contract(
    tmp_path: Path,
    document: object,
    message: str,
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(project_payload.PayloadManifestError, match=message):
        project_payload.load_manifest(path)


def test_verify_manifest_classifies_installed_drift(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    target = tmp_path / "target"
    manifest_path = tmp_path / "manifest.json"
    write(expected, "workspace/removed.py", "removed\n")
    write(expected, "workspace/modified.py", "before\n")
    write(expected, "workspace/same.py", "same\n")
    project_payload.write_manifest(expected, manifest_path, "abc123")
    write(target, "workspace/modified.py", "after\n")
    write(target, "workspace/same.py", "same\n")
    write(target, "workspace/added.py", "added\n")
    write(target, "data/runtime.db", "ignored\n")

    diff = project_payload.verify_manifest(
        target,
        project_payload.load_manifest(manifest_path),
    )

    assert diff.added == ("workspace/added.py",)
    assert diff.removed == ("workspace/removed.py",)
    assert diff.modified == ("workspace/modified.py",)
