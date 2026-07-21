from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"


def run_setup(
    *arguments: str,
    project_root: Path,
    aosp_root: Path,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PROJECT_ROOT": str(project_root),
            "AOSP_ROOT": str(aosp_root),
            "ANDROID_CONTEXT_SOURCE_COMMIT": "setup-test-commit",
            "PYTHON": os.environ.get("PYTHON", "python3"),
        }
    )
    return subprocess.run(
        ["bash", str(SETUP), *arguments],
        check=False,
        capture_output=True,
        text=True,
        input="",
        timeout=30,
        env=environment,
    )


def test_fresh_and_verify_only_work_without_aosp_or_stdin(tmp_path: Path) -> None:
    target = tmp_path / "custom-project"
    missing_aosp = tmp_path / "missing-aosp"

    fresh = run_setup(
        "--fresh",
        project_root=target,
        aosp_root=missing_aosp,
    )
    verify = run_setup(
        "--verify-only",
        project_root=target,
        aosp_root=missing_aosp,
    )

    assert fresh.returncode == 0, fresh.stderr
    assert verify.returncode == 0, verify.stderr
    assert (target / "workspace/cli.py").is_file()
    assert not (target / ".venv").exists()
    assert "PASS" in verify.stdout


def test_upgrade_uses_custom_project_root_and_preserves_runtime(tmp_path: Path) -> None:
    target = tmp_path / "custom-project"
    missing_aosp = tmp_path / "missing-aosp"
    assert run_setup(
        "--fresh",
        project_root=target,
        aosp_root=missing_aosp,
    ).returncode == 0
    marker = target / "data/marker"
    marker.parent.mkdir(parents=True)
    marker.write_text("keep\n", encoding="utf-8")

    upgrade = run_setup(
        "--upgrade",
        project_root=target,
        aosp_root=missing_aosp,
    )

    assert upgrade.returncode == 0, upgrade.stderr
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_rebuild_validates_aosp_before_installing(tmp_path: Path) -> None:
    target = tmp_path / "must-not-exist"
    missing_aosp = tmp_path / "missing-aosp"

    result = run_setup(
        "--fresh",
        "--rebuild",
        project_root=target,
        aosp_root=missing_aosp,
    )

    assert result.returncode != 0
    assert "aosp" in result.stderr.lower()
    assert not target.exists()


def test_setup_rejects_missing_mode_and_unknown_options(tmp_path: Path) -> None:
    target = tmp_path / "target"
    aosp = tmp_path / "aosp"

    missing = run_setup(project_root=target, aosp_root=aosp)
    unknown = run_setup(
        "--unknown",
        project_root=target,
        aosp_root=aosp,
    )

    assert missing.returncode != 0
    assert "usage" in missing.stderr.lower()
    assert unknown.returncode != 0
    assert "unknown" in unknown.stderr.lower()


def test_setup_contains_no_unconditional_prompt_or_embedded_payload() -> None:
    text = SETUP.read_text(encoding="utf-8")

    assert "read -p" not in text
    assert "cat >" not in text
    assert "base64 -d" not in text
    assert "installers/install_project.sh" in text
    result = subprocess.run(
        ["bash", "-n", str(SETUP)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
