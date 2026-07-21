from pathlib import Path
import json
import pytest

from workspace.config import load_workspace_config
from workspace.manifest import ManifestError, parse_repo_manifest
from workspace.languages import detect_languages
from workspace.registry import load_parser_registry
from workspace.planner import CoverageError, build_workspace_plan


def test_toml_supports_slash_names_and_extra_repository(tmp_path: Path) -> None:
    config = tmp_path / "roots.toml"
    config.write_text('''
[workspace]
aosp_root = "/aosp"
auto_discover_manifest = false
strict = false

[repositories."frameworks/base"]
enabled = true
include = ["core", "services"]
exclude = ["tests"]
languages = ["java", "aidl"]

[[extra_repositories]]
name = "local-extension"
path = "/src/local-extension"
enabled = true
''')
    value = load_workspace_config(config)
    assert value.repositories["frameworks/base"].include == ("core", "services")
    assert value.extra_repositories[0].name == "local-extension"


def test_manifest_include_and_cycle_detection(tmp_path: Path) -> None:
    root = tmp_path / "manifest.xml"
    child = tmp_path / "child.xml"
    root.write_text('<manifest><project name="base" path="frameworks/base"/><include name="child.xml"/></manifest>')
    child.write_text('<manifest><project name="perm" path="packages/modules/Permission"/></manifest>')
    assert [item.path for item in parse_repo_manifest(root)] == ["frameworks/base", "packages/modules/Permission"]
    child.write_text('<manifest><include name="manifest.xml"/></manifest>')
    with pytest.raises(ManifestError, match="cycle"):
        parse_repo_manifest(root)


def test_repo_wrapper_manifest_resolves_include_from_manifests_directory(tmp_path: Path) -> None:
    repo_dir = tmp_path / ".repo"
    manifests = repo_dir / "manifests"
    manifests.mkdir(parents=True)
    wrapper = repo_dir / "manifest.xml"
    wrapper.write_text('<manifest><include name="default.xml"/></manifest>')
    (manifests / "default.xml").write_text(
        '<manifest><project name="platform/frameworks/base" path="frameworks/base"/></manifest>'
    )
    assert [item.path for item in parse_repo_manifest(wrapper)] == ["frameworks/base"]


def test_language_inventory_honors_include_and_exclude(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/A.java").write_text("class A {}")
    (repo / "src/B.kt").write_text("class B")
    (repo / "src/C.rs").write_text("fn main() {}")
    (repo / "tests/T.java").write_text("class T {}")
    result = detect_languages(repo, ("src",), ("tests",), ())
    assert result.counts == {"java": 1, "kotlin": 1, "rust": 1}


def test_registry_is_capability_specific(tmp_path: Path) -> None:
    path = tmp_path / "registry.toml"
    path.write_text('''
[parsers.java]
implementation = "java_symbol_importer"
enabled = true
capabilities = ["symbols", "inheritance"]
[parsers.kotlin]
implementation = ""
enabled = false
capabilities = []
''')
    registry = load_parser_registry(path)
    assert registry.parser_for("java", "symbols").implementation == "java_symbol_importer"
    assert registry.parser_for("java", "binder") is None
    assert registry.parser_for("kotlin", "symbols") is None


def test_planner_reports_unsupported_and_strict_fails(tmp_path: Path) -> None:
    aosp = tmp_path / "aosp"
    repo = aosp / "frameworks/base"
    repo.mkdir(parents=True)
    (repo / "A.java").write_text("class A {}")
    (repo / "B.kt").write_text("class B")
    config = tmp_path / "roots.toml"
    config.write_text(f'''
[workspace]
aosp_root = "{aosp}"
auto_discover_manifest = false
strict = false
[repositories."frameworks/base"]
enabled = true
''')
    registry = tmp_path / "registry.toml"
    registry.write_text('''
[parsers.java]
implementation = "java_symbol_importer"
enabled = true
capabilities = ["symbols", "inheritance", "service_registration", "permission_enforcement"]
[parsers.kotlin]
implementation = ""
enabled = false
capabilities = []
''')
    plan = build_workspace_plan(config, registry)
    statuses = {(x.language, x.capability): x.status for x in plan.tasks}
    assert statuses[("java", "symbols")] == "scheduled"
    assert statuses[("kotlin", "symbols")] == "unsupported"
    with pytest.raises(CoverageError):
        build_workspace_plan(config, registry, strict=True)
