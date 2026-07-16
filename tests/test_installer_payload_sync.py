from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_installer_payload import PayloadError, extract_payload


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_multi_repository_source_configuration_v01.sh"
PAYLOADS = (
    (
        "workspace/build_publish.py",
        ROOT / "android-context-current/workspace/build_publish.py",
    ),
    (
        "tests/unit/test_build_publish.py",
        ROOT / "android-context-current/tests/unit/test_build_publish.py",
    ),
    (
        "tests/integration/test_atomic_rebuild.py",
        ROOT / "android-context-current/tests/integration/test_atomic_rebuild.py",
    ),
    (
        "scripts/rebuild_all.sh",
        ROOT / "android-context-current/scripts/rebuild_all.sh",
    ),
)


@pytest.mark.parametrize("target,snapshot", PAYLOADS)
def test_installer_payload_matches_snapshot(target: str, snapshot: Path) -> None:
    assert extract_payload(INSTALLER, target) == snapshot.read_text(encoding="utf-8")


def test_extract_payload_rejects_missing_target(tmp_path: Path) -> None:
    installer = tmp_path / "installer.sh"
    installer.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    with pytest.raises(PayloadError, match="missing payload"):
        extract_payload(installer, "workspace/missing.py")


def test_extract_payload_rejects_duplicate_target(tmp_path: Path) -> None:
    installer = tmp_path / "installer.sh"
    installer.write_text(
        "cat > workspace/module.py <<'PY'\nfirst\nPY\n"
        "cat > workspace/module.py <<'PY'\nsecond\nPY\n",
        encoding="utf-8",
    )

    with pytest.raises(PayloadError, match="duplicate payload"):
        extract_payload(installer, "workspace/module.py")


def test_extract_payload_rejects_unquoted_delimiter(tmp_path: Path) -> None:
    installer = tmp_path / "installer.sh"
    installer.write_text(
        "cat > workspace/module.py <<PY\ncontent\nPY\n",
        encoding="utf-8",
    )

    with pytest.raises(PayloadError, match="unquoted heredoc"):
        extract_payload(installer, "workspace/module.py")


def test_extract_payload_rejects_unterminated_payload(tmp_path: Path) -> None:
    installer = tmp_path / "installer.sh"
    installer.write_text(
        "cat > workspace/module.py <<'PY'\ncontent\n",
        encoding="utf-8",
    )

    with pytest.raises(PayloadError, match="unterminated payload"):
        extract_payload(installer, "workspace/module.py")
