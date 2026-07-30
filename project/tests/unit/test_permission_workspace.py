from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from workspace.planner import build_workspace_plan
from workspace.revisions import resolve_repository_revision


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def initialize_git_repository(path: Path) -> str:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "tracked.txt").write_text("revision\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git", "-C", str(path),
            "-c", "user.name=Permission Test",
            "-c", "user.email=permission@example.invalid",
            "commit", "-q", "-m", "fixture",
        ],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_permission_semantics_are_scheduled_with_revision_and_strict_intent(
    tmp_path: Path,
) -> None:
    aosp = tmp_path / "aosp"
    repository = aosp / "frameworks/base"
    (repository / "res/layout").mkdir(parents=True)
    (repository / "A.java").write_text("class A {}", encoding="utf-8")
    (repository / "B.kt").write_text("class B", encoding="utf-8")
    (repository / "AndroidManifest.xml").write_text(
        "<manifest package=\"example\" />",
        encoding="utf-8",
    )
    (repository / "res/layout/screen.xml").write_text(
        "<LinearLayout />",
        encoding="utf-8",
    )
    revision = initialize_git_repository(repository)
    config = tmp_path / "roots.toml"
    config.write_text(
        f'''[workspace]
aosp_root = "{aosp}"
auto_discover_manifest = false
[repositories."frameworks/base"]
enabled = true
''',
        encoding="utf-8",
    )

    plan = build_workspace_plan(
        config,
        PROJECT_ROOT / "config/parser_registry.toml",
        strict_capability="permission_semantics",
    )
    tasks = {(task.language, task.capability): task for task in plan.tasks}

    assert tasks[("xml", "permission_semantics")].status == "scheduled"
    assert tasks[("java", "permission_semantics")].status == "scheduled"
    assert tasks[("kotlin", "permission_semantics")].status == "scheduled"
    assert ("xml", "symbols") not in tasks
    assert plan.strict is True
    assert plan.strict_capability == "permission_semantics"
    assert plan.repositories[0].revision == revision
    payload = plan.to_dict()
    assert payload["strict"] is True
    assert payload["strict_capability"] == "permission_semantics"
    assert payload["repositories"][0]["revision"] == revision


def test_non_git_repository_revision_is_unknown_only_to_reporters(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "plain"
    repository.mkdir()

    assert resolve_repository_revision(repository) is None


def test_canonical_frameworks_base_scope_includes_permission_policy_xml() -> None:
    config = tomllib.loads(
        (PROJECT_ROOT / "config/source_roots.default.toml").read_text(
            encoding="utf-8"
        )
    )

    assert "data" in config["repositories"]["frameworks/base"]["include"]
