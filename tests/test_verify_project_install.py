from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import project_payload


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/verify_project_install.py"


def write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_cli(target: Path, manifest: Path | None = None) -> subprocess.CompletedProcess[str]:
    arguments = [sys.executable, str(CLI), "--target", str(target)]
    if manifest is not None:
        arguments.extend(["--manifest", str(manifest)])
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def test_cli_passes_for_matching_installed_payload(tmp_path: Path) -> None:
    target = tmp_path / "target"
    write(target, "workspace/module.py", "same\n")
    project_payload.write_manifest(
        target,
        target / project_payload.DEFAULT_MANIFEST_NAME,
        "abc123",
    )

    result = run_cli(target)

    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout
    assert "1 managed files" in result.stdout


def test_cli_reports_every_drift_category_and_returns_one(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    target = tmp_path / "target"
    manifest = tmp_path / "manifest.json"
    write(expected, "workspace/removed.py", "removed\n")
    write(expected, "workspace/modified.py", "before\n")
    project_payload.write_manifest(expected, manifest, "abc123")
    write(target, "workspace/modified.py", "after\n")
    write(target, "workspace/added.py", "added\n")

    result = run_cli(target, manifest)

    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert "added: workspace/added.py" in result.stdout
    assert "removed: workspace/removed.py" in result.stdout
    assert "modified: workspace/modified.py" in result.stdout


def test_cli_returns_two_for_missing_manifest(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()

    result = run_cli(target)

    assert result.returncode == 2
    assert "manifest" in result.stderr.lower()
