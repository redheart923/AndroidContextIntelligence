from __future__ import annotations

import hashlib
from pathlib import Path

from workspace.models import WorkspacePlan
from workspace.planner import build_workspace_plan
from workspace.platform_identity import detect_platform_identity


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    aosp = tmp_path / "aosp"
    repository = aosp / "frameworks/base"
    repository.mkdir(parents=True)
    (repository / "Demo.java").write_text("class Demo {}\n", encoding="utf-8")
    config = tmp_path / "source_roots.toml"
    config.write_text(
        f'''[workspace]
aosp_root = "{aosp.as_posix()}"
analysis_scope = "partial"
auto_discover_manifest = false

[repositories."frameworks/base"]
enabled = true
''',
        encoding="utf-8",
    )
    return aosp, config


def test_workspace_plan_supports_repeated_strict_capabilities(
    tmp_path: Path,
) -> None:
    _, config = _workspace(tmp_path)

    plan = build_workspace_plan(
        config,
        PROJECT_ROOT / "config/parser_registry.toml",
        strict_capabilities=("symbols", "permission_semantics"),
    )

    assert plan.strict_capabilities == ("permission_semantics", "symbols")
    assert plan.strict is True
    assert plan.to_dict()["strict_capabilities"] == [
        "permission_semantics",
        "symbols",
    ]
    assert "strict_capability" not in plan.to_dict()


def test_workspace_plan_reads_legacy_singular_strict_capability() -> None:
    payload = {
        "aosp_root": "/workspace",
        "analysis_scope": "partial",
        "full_aosp_coverage": False,
        "default_exclude": [],
        "strict": True,
        "strict_capability": "symbols",
        "repositories": [],
        "inventories": [],
        "tasks": [],
    }

    plan = WorkspacePlan.from_dict(payload)

    assert plan.strict_capabilities == ("symbols",)
    assert plan.to_dict()["strict_capabilities"] == ["symbols"]


def test_partial_workspace_without_version_files_has_unknown_platform(
    tmp_path: Path,
) -> None:
    aosp, _ = _workspace(tmp_path)

    identity = detect_platform_identity(aosp, None)

    assert identity.name == "unknown"
    assert identity.source == "unresolved"
    assert len(identity.fingerprint) == 64


def test_platform_override_is_stable_and_source_bound(tmp_path: Path) -> None:
    aosp, _ = _workspace(tmp_path)

    first = detect_platform_identity(aosp, "android-17")
    second = detect_platform_identity(aosp, "android-17")

    assert first == second
    assert first.name == "android-17"
    assert first.source == "override"
    assert first.fingerprint == hashlib.sha256(
        b'{"name":"android-17","source":"override"}'
    ).hexdigest()


def test_platform_identity_reads_version_defaults_when_available(
    tmp_path: Path,
) -> None:
    aosp, _ = _workspace(tmp_path)
    version_file = aosp / "build/make/core/version_defaults.mk"
    version_file.parent.mkdir(parents=True)
    version_file.write_text(
        "PLATFORM_VERSION := 17\nPLATFORM_VERSION_CODENAME := REL\n",
        encoding="utf-8",
    )

    identity = detect_platform_identity(aosp, None)

    assert identity.name == "17"
    assert identity.source == "build/make/core/version_defaults.mk"
