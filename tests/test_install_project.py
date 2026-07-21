from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.project_payload import (
    DEFAULT_MANIFEST_NAME,
    PAYLOAD_DIRECTORIES,
    PAYLOAD_FILES,
    compare_payload,
    load_manifest,
    verify_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/install_project.py"


def write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_source(root: Path, version: str) -> Path:
    for directory in PAYLOAD_DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for filename in PAYLOAD_FILES:
        write(root, filename, f"{filename} {version}\n")
    write(root, "workspace/version.py", f'VERSION = "{version}"\n')
    write(root, "scripts/rebuild_all.sh", "#!/usr/bin/env bash\nexit 0\n")
    write(root, "config/source_roots.toml", f'version = "{version}"\n')
    write(root, "configs/local.yaml", f"version: {version}\n")
    return root


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_fresh_install_promotes_verified_byte_equal_payload(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source", "v1")
    target = tmp_path / "target"

    result = run_cli(
        "--fresh",
        "--source",
        str(source),
        "--target",
        str(target),
        "--source-commit",
        "commit-v1",
    )

    assert result.returncode == 0, result.stderr
    assert compare_payload(source, target).is_clean
    manifest = load_manifest(target / DEFAULT_MANIFEST_NAME)
    assert manifest.source_commit == "commit-v1"
    assert verify_manifest(target, manifest).is_clean
    assert list(tmp_path.glob(".install-staging-*")) == []


def test_fresh_install_rejects_incomplete_source_without_touching_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incomplete"
    target = tmp_path / "target"
    write(source, "README.md", "incomplete\n")

    result = run_cli("--fresh", "--source", str(source), "--target", str(target))

    assert result.returncode == 2
    assert "missing payload" in result.stderr.lower()
    assert not target.exists()
    assert list(tmp_path.glob(".install-staging-*")) == []


def test_fresh_install_refuses_to_replace_existing_target(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source", "v1")
    target = tmp_path / "target"
    write(target, "sentinel.txt", "keep\n")

    result = run_cli("--fresh", "--source", str(source), "--target", str(target))

    assert result.returncode == 2
    assert "target already exists" in result.stderr.lower()
    assert (target / "sentinel.txt").read_text(encoding="utf-8") == "keep\n"


def test_upgrade_preserves_runtime_and_local_configuration(tmp_path: Path) -> None:
    source_v1 = make_source(tmp_path / "source-v1", "v1")
    source_v2 = make_source(tmp_path / "source-v2", "v2")
    target = tmp_path / "target"
    assert run_cli("--fresh", "--source", str(source_v1), "--target", str(target)).returncode == 0
    write(target, "data/runtime.db", "database\n")
    write(target, ".venv/marker", "environment\n")
    write(target, "config/source_roots.toml", "local roots\n")
    write(target, "configs/local.yaml", "local: true\n")
    write(target, "workspace/obsolete.py", "obsolete\n")

    result = run_cli(
        "--upgrade",
        "--source",
        str(source_v2),
        "--target",
        str(target),
        "--source-commit",
        "commit-v2",
    )

    assert result.returncode == 0, result.stderr
    assert (target / "workspace/version.py").read_text(encoding="utf-8") == 'VERSION = "v2"\n'
    assert not (target / "workspace/obsolete.py").exists()
    assert (target / "data/runtime.db").read_text(encoding="utf-8") == "database\n"
    assert (target / ".venv/marker").read_text(encoding="utf-8") == "environment\n"
    assert (target / "config/source_roots.toml").read_text(encoding="utf-8") == "local roots\n"
    assert (target / "configs/local.yaml").read_text(encoding="utf-8") == "local: true\n"
    manifest = load_manifest(target / DEFAULT_MANIFEST_NAME)
    assert manifest.source_commit == "commit-v2"
    assert verify_manifest(target, manifest).is_clean

    rollbacks = list(tmp_path.glob(".install-rollback-target-*"))
    assert len(rollbacks) == 1
    assert (rollbacks[0] / "workspace/version.py").read_text(encoding="utf-8") == 'VERSION = "v1"\n'
    assert not (rollbacks[0] / "data").exists()


def test_upgrade_restores_original_target_when_promotion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_project = importlib.import_module("scripts.install_project")
    source_v1 = make_source(tmp_path / "source-v1", "v1")
    source_v2 = make_source(tmp_path / "source-v2", "v2")
    target = tmp_path / "target"
    install_project.install_fresh(source_v1, target, "commit-v1")
    write(target, "data/runtime.db", "database\n")
    real_replace = install_project._replace
    calls = 0

    def fail_second_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(install_project, "_replace", fail_second_replace)

    with pytest.raises(install_project.InstallationError, match="promotion failure"):
        install_project.install_upgrade(source_v2, target, "commit-v2")

    assert (target / "workspace/version.py").read_text(encoding="utf-8") == 'VERSION = "v1"\n'
    assert (target / "data/runtime.db").read_text(encoding="utf-8") == "database\n"
    assert list(tmp_path.glob(".install-staging-*")) == []
    assert list(tmp_path.glob(".install-rollback-target-*")) == []


def test_verify_only_returns_one_for_managed_drift(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source", "v1")
    target = tmp_path / "target"
    assert run_cli("--fresh", "--source", str(source), "--target", str(target)).returncode == 0
    write(target, "workspace/version.py", "drift\n")

    result = run_cli("--verify-only", "--target", str(target))

    assert result.returncode == 1
    assert "modified: workspace/version.py" in result.stdout
