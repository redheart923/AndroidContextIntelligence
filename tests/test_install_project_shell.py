from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installers/install_project.sh"


def test_shell_installer_is_a_syntax_valid_thin_adapter() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    assert "scripts/install_project.py" in text
    assert "exec" in text
    assert "cat >" not in text
    assert "base64 -d" not in text
    assert "read -p" not in text
    result = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
