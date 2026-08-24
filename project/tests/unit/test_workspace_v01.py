from pathlib import Path
import json
import pytest

from workspace.config import load_workspace_config
from workspace.manifest import ManifestError, parse_repo_manifest
from workspace.languages import detect_languages
from workspace.registry import load_parser_registry
from workspace.planner import CoverageError, build_workspace_plan


def _scope_fixture(tmp_path: Path, scope_line: str = "") -> tuple[Path, Path]:
    aosp = tmp_path / "aosp"
    repository = aosp / "demo/repo"
    repository.mkdir(parents=True)
    (repository / "Demo.java").write_text("class Demo {}\n", encoding="utf-8")
    config = tmp_path / "roots.toml"
    config.write_text(
        f'''[workspace]
aosp_root = "{aosp.as_posix()}"
auto_discover_manifest = false
{scope_line}

[repositories."demo/repo"]
enabled = true
''',
        encoding="utf-8",
    )
    registry = tmp_path / "registry.toml"
    registry.write_text(
        '''[parsers.java]
implementation = "java_symbol_importer"
enabled = true
capabilities = ["symbols"]
''',
        encoding="utf-8",
    )
    return config, registry


def test_analysis_scope_defaults_to_aosp(tmp_path: Path) -> None:
    config, registry = _scope_fixture(tmp_path)

    loaded = load_workspace_config(config)
    payload = build_workspace_plan(config, registry).to_dict()

    assert loaded.analysis_scope == "aosp"
    assert payload["analysis_scope"] == "aosp"
    assert payload["full_aosp_coverage"] is False


def test_partial_scope_round_trips_through_plan(tmp_path: Path) -> None:
    config, registry = _scope_fixture(
        tmp_path,
        'analysis_scope = "partial"',
    )

    loaded = load_workspace_config(config)
    payload = build_workspace_plan(config, registry).to_dict()

    assert loaded.analysis_scope == "partial"
    assert payload["analysis_scope"] == "partial"
    assert payload["full_aosp_coverage"] is False


@pytest.mark.parametrize("value", ["", "AOSP", "full", "vendor"])
def test_unknown_analysis_scope_is_rejected(tmp_path: Path, value: str) -> None:
    config, _ = _scope_fixture(
        tmp_path,
        f'analysis_scope = "{value}"',
    )

    with pytest.raises(ValueError, match="workspace.analysis_scope"):
        load_workspace_config(config)


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


def test_local_config_overlays_defaults_without_dropping_required_roots(
    tmp_path: Path,
) -> None:
    defaults = tmp_path / "source_roots.default.toml"
    local = tmp_path / "source_roots.local.toml"
    defaults.write_text(
        """
[workspace]
aosp_root = "/canonical/aosp"
auto_discover_manifest = true
strict = false
[defaults]
exclude = ["tests"]
[repositories."frameworks/base"]
enabled = true
include = ["core", "services", "data"]
exclude = ["benchmarks"]
"""
    )
    local.write_text(
        """
[workspace]
aosp_root = "/local/aosp"
[repositories."frameworks/base"]
enabled = false
include = ["vendor-extension"]
exclude = ["local-tests"]
"""
    )

    value = load_workspace_config(defaults, local)

    assert value.aosp_root == Path("/local/aosp")
    assert value.repositories["frameworks/base"].enabled is False
    assert value.repositories["frameworks/base"].include == (
        "core", "services", "data", "vendor-extension",
    )
    assert value.repositories["frameworks/base"].exclude == (
        "benchmarks", "local-tests",
    )


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


def test_registry_exposes_per_capability_quality(tmp_path: Path) -> None:
    path = tmp_path / "registry.toml"
    path.write_text(
        """
[parsers.kotlin]
implementation = "kotlin_ctags_importer"
enabled = true
capabilities = ["symbols", "permission_semantics"]

[parsers.kotlin.capability_quality]
symbols = "tags_only"
permission_semantics = "heuristic"
"""
    )

    parser = load_parser_registry(path).parser_for("kotlin", "symbols")

    assert parser is not None
    assert parser.quality_for("symbols") == "tags_only"
    assert parser.quality_for("permission_semantics") == "heuristic"


def test_registry_exposes_required_runtime_evidence(tmp_path: Path) -> None:
    path = tmp_path / "registry.toml"
    path.write_text(
        """
[parsers.java]
implementation = "java_symbol_importer"
enabled = true
capabilities = ["symbols"]

[parsers.java.capability_quality]
symbols = "tags_only"

[parsers.java.capability_evidence]
symbols = ["node_type_prefix:JAVA_"]
"""
    )

    parser = load_parser_registry(path).parser_for("java", "symbols")

    assert parser is not None
    assert parser.evidence_for("symbols") == ("node_type_prefix:JAVA_",)


def test_capability_specific_implementation_overrides_default(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.toml"
    path.write_text(
        """
[parsers.java]
implementation = "java_symbol_importer"
enabled = true
capabilities = ["symbols", "call_graph"]

[parsers.java.capability_implementations]
call_graph = "codeql_java_kotlin_importer"

[parsers.java.capability_quality]
symbols = "tags_only"
call_graph = "semantic"

[parsers.java.capability_evidence]
symbols = ["node_type_prefix:JAVA_"]
call_graph = ["typed_table:call_site"]
""",
        encoding="utf-8",
    )

    parser = load_parser_registry(path)["java"]

    assert parser.implementation_for("symbols") == "java_symbol_importer"
    assert parser.implementation_for("call_graph") == (
        "codeql_java_kotlin_importer"
    )


def test_canonical_kotlin_capabilities_do_not_claim_inheritance() -> None:
    project_root = Path(__file__).resolve().parents[2]
    registry = load_parser_registry(project_root / "config/parser_registry.toml")
    kotlin = registry["kotlin"]

    assert kotlin.quality_for("symbols") == "tags_only"
    assert kotlin.quality_for("service_registration") == "heuristic"
    assert kotlin.quality_for("permission_semantics") == "heuristic"
    assert registry.parser_for("kotlin", "inheritance") is None


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
capabilities = ["symbols", "inheritance", "service_registration", "permission_semantics"]
[parsers.kotlin]
implementation = ""
enabled = false
capabilities = []
''')
    plan = build_workspace_plan(config, registry)
    statuses = {
        (x.language, x.capability): (x.status, x.quality)
        for x in plan.tasks
    }
    assert statuses[("java", "symbols")] == ("scheduled", "semantic")
    assert statuses[("kotlin", "symbols")] == ("unsupported", None)
    with pytest.raises(CoverageError):
        build_workspace_plan(config, registry, strict=True)
