from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = (
    "setup_android_context_intelligence_v1.sh",
    "install_java_inheritance_graph_v01.sh",
    "install_system_service_registration_graph_v01.sh",
    "install_multi_repository_source_configuration_v01.sh",
    "install_vendor_customization_graph_v01.sh",
    "install_permission_enforcement_graph_v01.sh",
)
FORBIDDEN_PAYLOAD_MARKERS = (
    "cat >",
    "base64 -d",
    "read -p",
    "workspace/build_publish.py",
    "workspace/multi_permission.py",
    "scripts/rebuild_all.sh <<",
)


@pytest.mark.parametrize("name", WRAPPERS)
def test_compatibility_wrapper_delegates_without_owning_payload(name: str) -> None:
    wrapper = ROOT / "installers" / name
    text = wrapper.read_text(encoding="utf-8")

    assert "setup.sh" in text
    assert "exec" in text
    assert "--upgrade" in text
    for marker in FORBIDDEN_PAYLOAD_MARKERS:
        assert marker not in text


@pytest.mark.parametrize("name", WRAPPERS)
def test_compatibility_wrapper_passes_bash_syntax(name: str) -> None:
    wrapper = ROOT / "installers" / name
    result = subprocess.run(
        ["bash", "-n", str(wrapper)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_obsolete_self_extracting_payload_verifier_is_removed() -> None:
    assert not (ROOT / "scripts/verify_installer_payload.py").exists()
