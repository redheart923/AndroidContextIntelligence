#!/usr/bin/env bash
set -Eeuo pipefail

AOSP_ROOT="${AOSP_ROOT:-/home/ts/aosp}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/ts/android-context-intelligence}"
DB_PATH="$PROJECT_ROOT/data/android_context.db"
STAMP="$(date +%Y%m%d-%H%M%S)"

log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf '\n[ERROR] %s\n' "$*" >&2; exit 1; }
trap 'die "failed at line $LINENO"' ERR

for command in python3 ctags sqlite3; do
    command -v "$command" >/dev/null 2>&1 || die "Missing command: $command"
done
[[ -d "$AOSP_ROOT" ]] || die "Missing AOSP root: $AOSP_ROOT"
[[ -d "$PROJECT_ROOT" ]] || die "Missing project root: $PROJECT_ROOT"
[[ -f "$PROJECT_ROOT/storage/schema.sql" ]] || die "Missing storage/schema.sql"
[[ -f "$PROJECT_ROOT/collectors/source/ctags_importer.py" ]] || die "Install the v1 baseline first"
[[ -f "$PROJECT_ROOT/collectors/binder/aidl_binder_importer.py" ]] || die "Install the v1 baseline first"
[[ -f "$PROJECT_ROOT/collectors/source/java_inheritance_importer.py" ]] || die "Install Java Inheritance Graph v0.1 first"
[[ -f "$PROJECT_ROOT/collectors/service/service_registration_importer.py" ]] || die "Install Service Registration Graph v0.1 first"

cd "$PROJECT_ROOT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

log "Backing up canonical rebuild and documentation"
mkdir -p backups/multi-repository-v01
for path in scripts/rebuild_all.sh README.md INSTALLATION_MANIFEST.txt; do
    [[ -f "$path" ]] && cp -a "$path" "backups/multi-repository-v01/$(basename "$path").$STAMP"
done

mkdir -p workspace config data/workspace data/raw/ctags data/raw/aidl data/raw/inheritance data/raw/service tests/unit tests/integration queries scripts
touch workspace/__init__.py

log "Writing RED tests"
cat > tests/unit/test_workspace_v01.py <<'PY'
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
PY

if [[ ! -f workspace/manifest.py ]]; then
    if python -m pytest -q tests/unit/test_workspace_v01.py > data/workspace/red.log 2>&1; then
        die "RED tests unexpectedly passed on a clean installation"
    else
        log "RED confirmed: workspace modules are absent"
    fi
else
    log "Existing workspace modules detected; skipping clean-install RED gate"
fi

log "Writing workspace models and configuration loader"
cat > workspace/models.py <<'PY'
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepositoryOverride:
    path: str
    enabled: bool = False
    name: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtraRepository:
    name: str
    path: str
    enabled: bool = True
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceConfig:
    aosp_root: Path
    auto_discover_manifest: bool = True
    auto_enable_discovered: bool = False
    strict: bool = False
    default_exclude: tuple[str, ...] = ()
    repositories: dict[str, RepositoryOverride] = field(default_factory=dict)
    extra_repositories: tuple[ExtraRepository, ...] = ()


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    path: str
    enabled: bool = False
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    source: str = "manifest"
    status: str = "available"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "enabled": self.enabled,
                "include": list(self.include), "exclude": list(self.exclude),
                "languages": list(self.languages), "source": self.source,
                "status": self.status}


@dataclass(frozen=True)
class LanguageInventory:
    repository: str
    counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {"repository": self.repository, "counts": dict(sorted(self.counts.items()))}


@dataclass(frozen=True)
class ParserSpec:
    language: str
    implementation: str
    enabled: bool
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class PlanTask:
    repository: str
    repository_path: str
    language: str
    capability: str
    parser: str | None
    status: str
    files: int

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WorkspacePlan:
    aosp_root: str
    repositories: tuple[RepositorySpec, ...]
    inventories: tuple[LanguageInventory, ...]
    tasks: tuple[PlanTask, ...]
    default_exclude: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"aosp_root": self.aosp_root,
                "default_exclude": list(self.default_exclude),
                "repositories": [x.to_dict() for x in self.repositories],
                "inventories": [x.to_dict() for x in self.inventories],
                "tasks": [x.to_dict() for x in self.tasks]}
PY

cat > workspace/config.py <<'PY'
from __future__ import annotations
import tomllib
from pathlib import Path
from .models import ExtraRepository, RepositoryOverride, WorkspaceConfig

KNOWN = {"java", "aidl", "kotlin", "c", "cpp", "rust", "hidl", "python", "blueprint", "make", "proto"}


def _strings(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{field} must be an array of strings")
    return tuple(value)


def _languages(value: object, field: str) -> tuple[str, ...]:
    result = _strings(value, field)
    unknown = sorted(set(result) - KNOWN)
    if unknown:
        raise ValueError(f"unknown languages in {field}: {', '.join(unknown)}")
    return result


def load_workspace_config(path: Path) -> WorkspaceConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    workspace = data.get("workspace", {})
    defaults = data.get("defaults", {})
    if not workspace.get("aosp_root"):
        raise ValueError("workspace.aosp_root is required")
    repositories: dict[str, RepositoryOverride] = {}
    for repo_path, item in data.get("repositories", {}).items():
        repositories[repo_path] = RepositoryOverride(
            path=repo_path, enabled=bool(item.get("enabled", False)),
            name=item.get("name"), include=_strings(item.get("include"), f"{repo_path}.include"),
            exclude=_strings(item.get("exclude"), f"{repo_path}.exclude"),
            languages=_languages(item.get("languages"), f"{repo_path}.languages"))
    extras = tuple(ExtraRepository(
        name=item["name"], path=item["path"], enabled=bool(item.get("enabled", True)),
        include=_strings(item.get("include"), "extra.include"),
        exclude=_strings(item.get("exclude"), "extra.exclude"),
        languages=_languages(item.get("languages"), "extra.languages"))
        for item in data.get("extra_repositories", []))
    return WorkspaceConfig(
        aosp_root=Path(workspace["aosp_root"]),
        auto_discover_manifest=bool(workspace.get("auto_discover_manifest", True)),
        auto_enable_discovered=bool(workspace.get("auto_enable_discovered", False)),
        strict=bool(workspace.get("strict", False)),
        default_exclude=_strings(defaults.get("exclude"), "defaults.exclude"),
        repositories=repositories, extra_repositories=extras)
PY

cat > workspace/manifest.py <<'PY'
from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path
from .models import RepositorySpec


class ManifestError(RuntimeError):
    pass


def parse_repo_manifest(manifest_path: Path) -> tuple[RepositorySpec, ...]:
    found: dict[str, RepositorySpec] = {}
    active: list[Path] = []

    def repo_metadata_directory(path: Path) -> Path | None:
        for candidate in (path, *path.parents):
            if candidate.name == ".repo":
                return candidate
        return None

    def resolve_include(current_manifest: Path, include_name: str) -> Path:
        direct = current_manifest.parent / include_name
        if direct.is_file():
            return direct
        repo_dir = repo_metadata_directory(current_manifest)
        if repo_dir is not None:
            manifest_store = repo_dir / "manifests" / include_name
            if manifest_store.is_file():
                return manifest_store
            local_store = repo_dir / "local_manifests" / include_name
            if local_store.is_file():
                return local_store
        return direct

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in active:
            raise ManifestError(f"manifest include cycle: {resolved}")
        if not resolved.is_file():
            raise ManifestError(f"missing manifest include: {resolved}")
        active.append(resolved)
        try:
            root = ET.parse(resolved).getroot()
            for project in root.findall("project"):
                name = project.get("name")
                if not name:
                    continue
                repo_path = project.get("path") or name
                found.setdefault(repo_path, RepositorySpec(name=name, path=repo_path))
            for include in root.findall("include"):
                name = include.get("name")
                if name:
                    visit(resolve_include(resolved, name))
        except ET.ParseError as error:
            raise ManifestError(f"invalid manifest {resolved}: {error}") from error
        finally:
            active.pop()

    visit(manifest_path)
    return tuple(found[key] for key in sorted(found))
PY

cat > workspace/languages.py <<'PY'
from __future__ import annotations
import os
from collections import Counter
from pathlib import Path
from .models import LanguageInventory

SUFFIXES = {".java": "java", ".aidl": "aidl", ".kt": "kotlin", ".kts": "kotlin",
            ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
            ".hpp": "cpp", ".hh": "cpp", ".rs": "rust", ".hal": "hidl",
            ".py": "python", ".proto": "proto", ".mk": "make"}


def _excluded(relative: Path, patterns: tuple[str, ...]) -> bool:
    parts = set(relative.parts)
    for pattern in patterns:
        if pattern in parts or relative.match(pattern) or relative.match(f"**/{pattern}"):
            return True
    return False


def source_paths(repository: Path, include: tuple[str, ...]) -> list[Path]:
    if not include:
        return [repository]
    return [repository / item for item in include if (repository / item).exists()]


def detect_languages(repository: Path, include: tuple[str, ...], exclude: tuple[str, ...], whitelist: tuple[str, ...]) -> LanguageInventory:
    counts: Counter[str] = Counter()
    for root in source_paths(repository, include):
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            dirs[:] = [d for d in dirs if not _excluded((current_path / d).relative_to(repository), exclude)]
            for name in files:
                path = current_path / name
                relative = path.relative_to(repository)
                if _excluded(relative, exclude):
                    continue
                if name == "Android.bp": language = "blueprint"
                elif name == "Android.mk": language = "make"
                else: language = SUFFIXES.get(path.suffix.lower())
                if language and (not whitelist or language in whitelist):
                    counts[language] += 1
    return LanguageInventory(repository=repository.name, counts=dict(sorted(counts.items())))
PY

cat > workspace/registry.py <<'PY'
from __future__ import annotations
import tomllib
from pathlib import Path
from .models import ParserSpec


BUILTINS = {
    "java": ParserSpec("java", "java_symbol_importer", True,
        ("symbols", "inheritance", "service_registration", "permission_enforcement")),
    "aidl": ParserSpec("aidl", "aidl_binder_importer", True, ("symbols", "binder")),
}


class ParserRegistry(dict[str, ParserSpec]):
    def parser_for(self, language: str, capability: str) -> ParserSpec | None:
        value = self.get(language)
        if not value or not value.enabled or not value.implementation or capability not in value.capabilities:
            return None
        return value


def load_parser_registry(path: Path) -> ParserRegistry:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    result = ParserRegistry(BUILTINS)
    for language, item in data.get("parsers", {}).items():
        capabilities = item.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities):
            raise ValueError(f"invalid capabilities for {language}")
        result[language] = ParserSpec(language=language,
            implementation=str(item.get("implementation", "")),
            enabled=bool(item.get("enabled", False)), capabilities=tuple(capabilities))
    return result
PY

cat > workspace/planner.py <<'PY'
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from .config import load_workspace_config
from .languages import detect_languages
from .manifest import parse_repo_manifest
from .models import LanguageInventory, PlanTask, RepositorySpec, WorkspacePlan
from .registry import load_parser_registry

CAPABILITIES = {"java": ("symbols", "inheritance", "service_registration", "permission_enforcement"),
                "aidl": ("symbols", "binder"), "kotlin": ("symbols", "inheritance", "service_registration", "permission_enforcement"),
                "c": ("symbols", "native_binder"), "cpp": ("symbols", "native_binder"),
                "rust": ("symbols", "native_binder"), "hidl": ("symbols", "binder"),
                "python": ("symbols",), "blueprint": ("build",), "make": ("build",), "proto": ("symbols",)}


class CoverageError(RuntimeError):
    pass


def build_workspace_plan(config_path: Path, registry_path: Path, strict: bool = False, strict_capability: str | None = None) -> WorkspacePlan:
    config = load_workspace_config(config_path)
    registry = load_parser_registry(registry_path)
    repos: dict[str, RepositorySpec] = {}
    manifest = config.aosp_root / ".repo/manifest.xml"
    if config.auto_discover_manifest and manifest.is_file():
        for item in parse_repo_manifest(manifest):
            repos[item.path] = replace(item, enabled=config.auto_enable_discovered)
    for path, override in config.repositories.items():
        previous = repos.get(path, RepositorySpec(name=override.name or path, path=path, source="config"))
        repos[path] = replace(previous, name=override.name or previous.name, enabled=override.enabled,
            include=override.include, exclude=override.exclude, languages=override.languages, source="config")
    for item in config.extra_repositories:
        repos[item.path] = RepositorySpec(name=item.name, path=item.path, enabled=item.enabled,
            include=item.include, exclude=item.exclude, languages=item.languages, source="extra")
    normalized: list[RepositorySpec] = []
    inventories: list[LanguageInventory] = []
    tasks: list[PlanTask] = []
    gaps: list[PlanTask] = []
    for key in sorted(repos):
        repo = repos[key]
        location = Path(repo.path) if Path(repo.path).is_absolute() else config.aosp_root / repo.path
        status = "available" if location.is_dir() else "missing"
        repo = replace(repo, status=status)
        normalized.append(repo)
        if not repo.enabled or status != "available":
            if repo.enabled and status == "missing": gaps.append(PlanTask(repo.name, repo.path, "repository", "availability", None, "missing_repository", 0))
            continue
        inventory = detect_languages(location, repo.include, tuple(config.default_exclude) + tuple(repo.exclude), repo.languages)
        inventory = replace(inventory, repository=repo.name)
        inventories.append(inventory)
        for language, count in sorted(inventory.counts.items()):
            for capability in CAPABILITIES.get(language, ("symbols",)):
                parser = registry.parser_for(language, capability)
                status = "scheduled" if parser else "unsupported"
                task = PlanTask(repo.name, repo.path, language, capability,
                    parser.implementation if parser else None, status, count)
                tasks.append(task)
                if status != "scheduled" and (strict_capability is None or strict_capability == capability): gaps.append(task)
    effective_strict = strict or config.strict or strict_capability is not None
    if effective_strict and gaps:
        sample = ", ".join(f"{x.repository}:{x.language}:{x.capability}:{x.status}" for x in gaps[:8])
        raise CoverageError(f"workspace coverage gaps: {sample}")
    return WorkspacePlan(str(config.aosp_root), tuple(normalized), tuple(inventories), tuple(tasks), config.default_exclude)
PY

cat > workspace/cli.py <<'PY'
from __future__ import annotations
import argparse, json, os, tempfile
from pathlib import Path
from .planner import CoverageError, build_workspace_plan


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--strict-capability")
    args = parser.parse_args()
    try:
        plan = build_workspace_plan(args.config, args.registry, args.strict, args.strict_capability)
    except CoverageError as error:
        print(f"ERROR: {error}")
        return 4
    value = plan.to_dict()
    atomic_json(args.out_dir / "repositories.json", value["repositories"])
    atomic_json(args.out_dir / "language-inventory.json", value["inventories"])
    atomic_json(args.out_dir / "execution-plan.json", value)
    coverage = [{"repository": x["repository"], "language": x["language"],
                 "capability": x["capability"], "parser": x["parser"],
                 "status": x["status"], "files": x["files"]} for x in value["tasks"]]
    atomic_json(args.out_dir / "capability-report.json", coverage)
    enabled = sum(1 for x in value["repositories"] if x["enabled"])
    unsupported = sum(1 for x in coverage if x["status"] == "unsupported")
    print(f"Repositories discovered: {len(value['repositories'])}; enabled: {enabled}; unsupported capability entries: {unsupported}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
PY

log "Writing multi-repository pipeline adapters"
cat > workspace/pipeline.py <<'PY'
from __future__ import annotations
import argparse, json, sqlite3, subprocess, sys
from pathlib import Path


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def repositories_for(plan: dict, language: str, capability: str) -> list[dict]:
    names = {x["repository"] for x in plan["tasks"] if x["language"] == language and x["capability"] == capability and x["status"] == "scheduled"}
    return [x for x in plan["repositories"] if x["name"] in names and x["enabled"] and x["status"] == "available"]


def slug(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in value).strip("-")


def absolute_repo(aosp: Path, repo: dict) -> Path:
    path = Path(repo["path"])
    return path if path.is_absolute() else aosp / path


def scan_paths(aosp: Path, repo: dict) -> list[Path]:
    root = absolute_repo(aosp, repo)
    return [root / x for x in repo.get("include", []) if (root / x).exists()] or [root]


def repository_for_source(aosp: Path, repositories: list[dict], source: Path | str) -> dict | None:
    path = Path(source).resolve()
    candidates = sorted(repositories, key=lambda x: len(str(absolute_repo(aosp, x))), reverse=True)
    for repo in candidates:
        root = absolute_repo(aosp, repo).resolve()
        try:
            path.relative_to(root)
            return repo
        except ValueError:
            continue
    return None


def source_allowed(aosp: Path, repo: dict, source: Path | str, defaults: list[str]) -> bool:
    path = Path(source).resolve(); root = absolute_repo(aosp, repo).resolve()
    try: relative = path.relative_to(root)
    except ValueError: return False
    patterns = list(defaults) + list(repo.get("exclude", []))
    return not any(pattern in relative.parts or relative.match(pattern) or relative.match(f"**/{pattern}") for pattern in patterns)


def node_sources(db: Path) -> dict[str, str | None]:
    with sqlite3.connect(db) as c:
        return dict(c.execute("SELECT node_id, source_path FROM node"))


def run_java(plan: dict, db: Path, raw_dir: Path) -> list[dict]:
    aosp = Path(plan["aosp_root"]); raw_dir.mkdir(parents=True, exist_ok=True)
    duplicates: list[dict] = []
    for repo in repositories_for(plan, "java", "symbols"):
        output = raw_dir / f"{slug(repo['name'])}.jsonl"
        command = ["ctags", "--languages=Java", "--output-format=json", "--fields=+nKSEi", "-R", "-f", str(output)]
        for pattern in list(plan.get("default_exclude", [])) + list(repo.get("exclude", [])):
            command.append(f"--exclude={pattern}")
        command.extend(str(x) for x in scan_paths(aosp, repo))
        subprocess.run(command, check=True)
        before = node_sources(db)
        subprocess.run([sys.executable, "-m", "collectors.source.ctags_importer", str(output), str(db), str(aosp)], check=True)
        after = node_sources(db)
        for node_id, old_path in before.items():
            new_path = after.get(node_id)
            if old_path and new_path and old_path != new_path:
                duplicates.append({"node_id": node_id, "first_source": old_path, "replacement_source": new_path, "repository": repo["name"]})
    (raw_dir / "duplicate-qualified-names.json").write_text(json.dumps(duplicates, indent=2), encoding="utf-8")
    return duplicates


def run_inheritance(plan_path: Path, plan: dict, db: Path, raw_dir: Path, report_dir: Path) -> None:
    aosp = Path(plan["aosp_root"]); report_dir.mkdir(parents=True, exist_ok=True)
    for repo in repositories_for(plan, "java", "inheritance"):
        source = raw_dir / f"{slug(repo['name'])}.jsonl"
        if source.is_file():
            subprocess.run([sys.executable, "-m", "collectors.source.java_inheritance_importer",
                "--ctags-jsonl", str(source), "--source-root", str(aosp), "--db", str(db),
                "--report", str(report_dir / f"{slug(repo['name'])}.json")], check=True)


def annotate(db: Path, plan: dict) -> None:
    repos = sorted((x for x in plan["repositories"] if x["enabled"]), key=lambda x: len(x["path"]), reverse=True)
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT node_id, source_path, properties_json FROM node WHERE source_path IS NOT NULL").fetchall()
        for node_id, source, raw in rows:
            repo = next((x for x in repos if source == x["path"] or source.startswith(x["path"].rstrip("/") + "/")), None)
            if not repo: continue
            props = json.loads(raw or "{}")
            props.update({"repository": repo["name"], "repository_path": repo["path"],
                          "repository_relative_path": source[len(repo["path"]):].lstrip("/")})
            c.execute("UPDATE node SET properties_json=? WHERE node_id=?", (json.dumps(props, sort_keys=True), node_id))


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("command", choices=["java", "inheritance", "annotate"])
    p.add_argument("--plan", type=Path, required=True); p.add_argument("--db", type=Path, required=True)
    p.add_argument("--ctags-dir", type=Path, default=Path("data/raw/ctags")); p.add_argument("--report-dir", type=Path, default=Path("data/raw/inheritance"))
    a = p.parse_args(); plan = load_plan(a.plan)
    if a.command == "java": run_java(plan, a.db, a.ctags_dir)
    elif a.command == "inheritance": run_inheritance(a.plan, plan, a.db, a.ctags_dir, a.report_dir)
    else: annotate(a.db, plan)
    return 0


if __name__ == "__main__": raise SystemExit(main())
PY

cat > workspace/multi_aidl.py <<'PY'
from __future__ import annotations
import argparse, json
from pathlib import Path
from graph.writer import GraphWriter
from collectors.binder.aidl_binder_importer import (scan_aidl_files, build_simple_name_index,
    scan_java_binder_relations, import_aidl_interfaces, import_binder_relations)
from workspace.pipeline import (load_plan, repositories_for, scan_paths,
    repository_for_source, source_allowed)
from graph.writer import stable_id
import sqlite3


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--plan",type=Path,required=True);p.add_argument("--db",type=Path,required=True);p.add_argument("--report",type=Path,required=True);a=p.parse_args()
    plan=load_plan(a.plan); root=Path(plan["aosp_root"]); interfaces=[]; failures=[]
    repos=repositories_for(plan,"aidl","symbols")
    defaults=plan.get("default_exclude",[])
    for repo in repos:
        for source in scan_paths(root,repo):
            found, errors=scan_aidl_files(source)
            interfaces.extend(x for x in found if source_allowed(root,repo,x.source_path,defaults))
            failures.extend((x,e) for x,e in errors if source_allowed(root,repo,x,defaults))
    index=build_simple_name_index(interfaces); relations=[]
    for repo in repositories_for(plan,"java","symbols"):
        for source in scan_paths(root,repo):
            relations.extend(x for x in scan_java_binder_relations(source,index)
                if source_allowed(root,repo,x.source_path,defaults))
    writer=GraphWriter(a.db)
    try: ic,mc=import_aidl_interfaces(writer,interfaces,root)
    finally: writer.close()
    writer=GraphWriter(a.db)
    try: rc,uc=import_binder_relations(writer,a.db,relations,root)
    finally: writer.close()
    a.report.parent.mkdir(parents=True,exist_ok=True)
    unresolved=[]
    with sqlite3.connect(a.db) as connection:
        for relation in relations:
            if (not connection.execute("SELECT 1 FROM node WHERE node_id=?",(stable_id("JAVA_CLASS",relation.implementation_qname),)).fetchone()
                or not connection.execute("SELECT 1 FROM node WHERE node_id=?",(stable_id("AIDL_INTERFACE",relation.aidl_qname),)).fetchone()):
                repo=repository_for_source(root,plan["repositories"],relation.source_path)
                unresolved.append({"implementation":relation.implementation_qname,"aidl_interface":relation.aidl_qname,
                    "source_path":str(relation.source_path),"repository":repo["name"] if repo else None})
    a.report.write_text(json.dumps({"summary":{"interfaces":ic,"methods":mc,"binder_relations":rc,"unresolved":uc,"failures":len(failures)},
        "repositories":[x["name"] for x in repos],"unresolved_binder_relations":unresolved,
        "failures":[{"path":str(x),"error":str(e),"repository":(repository_for_source(root,repos,x) or {}).get("name")} for x,e in failures]},indent=2),encoding="utf-8")
    print(f"AIDL interfaces: {ic}; methods: {mc}; Binder relations: {rc}; unresolved: {uc}")
    return 0
if __name__=="__main__": raise SystemExit(main())
PY

cat > tests/integration/test_multi_repository_pipeline.py <<'PY'
from __future__ import annotations
import json, sqlite3, subprocess, sys
from pathlib import Path

from workspace.cli import atomic_json
from workspace.pipeline import load_plan, run_java, run_inheritance
from workspace.planner import build_workspace_plan


def test_two_repositories_flow_through_all_installed_graph_layers(tmp_path: Path) -> None:
    aosp = tmp_path / "aosp"
    base = aosp / "frameworks/base"
    vendor = aosp / "vendor/demo"
    base.mkdir(parents=True); vendor.mkdir(parents=True)
    (base / "Base.java").write_text("package common; public class Base {}", encoding="utf-8")
    (base / "IDemo.aidl").write_text("package demo; interface IDemo { void ping(); }", encoding="utf-8")
    (base / "DemoService.java").write_text("package demo; public class DemoService extends IDemo.Stub {}", encoding="utf-8")
    (vendor / "Child.java").write_text('''
package vendor.demo;
import common.Base;
public class Child extends Base {}
class Registrar { void register() { ServiceManager.addService("vendor.demo", new Child()); } }
''', encoding="utf-8")
    config = tmp_path / "roots.toml"
    config.write_text(f'''
[workspace]
aosp_root = "{aosp}"
auto_discover_manifest = false
[repositories."frameworks/base"]
enabled = true
[repositories."vendor/demo"]
enabled = true
''', encoding="utf-8")
    registry = Path("config/parser_registry.toml")
    plan = build_workspace_plan(config, registry)
    plan_path = tmp_path / "execution-plan.json"
    atomic_json(plan_path, plan.to_dict())
    db = tmp_path / "graph.db"
    with sqlite3.connect(db) as connection:
        connection.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
    ctags_dir = tmp_path / "ctags"
    run_java(load_plan(plan_path), db, ctags_dir)
    subprocess.run([sys.executable, "-m", "workspace.multi_aidl", "--plan", str(plan_path),
        "--db", str(db), "--report", str(tmp_path / "aidl.json")], check=True)
    run_inheritance(plan_path, load_plan(plan_path), db, ctags_dir, tmp_path / "inheritance")
    subprocess.run([sys.executable, "-m", "workspace.multi_service", "--plan", str(plan_path),
        "--db", str(db), "--report", str(tmp_path / "service.json")], check=True)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT 1 FROM edge WHERE edge_type='IMPLEMENTS_BINDER'").fetchone()
        assert connection.execute("SELECT 1 FROM edge WHERE edge_type='EXTENDS' AND source_path LIKE 'vendor/demo/%'").fetchone()
        assert connection.execute("SELECT 1 FROM node WHERE node_type='BINDER_SERVICE_NAME' AND qualified_name='vendor.demo'").fetchone()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
PY

cat > workspace/multi_service.py <<'PY'
from __future__ import annotations
import argparse,json,sqlite3
from collections import defaultdict
from pathlib import Path
from graph.writer import GraphWriter
from collectors.service.service_registration_importer import (scan_sources,ConstantResolver,DbTypeIndex,find_registration_calls,build_fact,import_fact)
from workspace.pipeline import load_plan,repositories_for,scan_paths,source_allowed


def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--plan",type=Path,required=True);p.add_argument("--db",type=Path,required=True);p.add_argument("--report",type=Path,required=True);a=p.parse_args()
    plan=load_plan(a.plan);root=Path(plan["aosp_root"]);sources=[];source_repo={}
    defaults=plan.get("default_exclude",[])
    for repo in repositories_for(plan,"java","service_registration"):
        for path in scan_paths(root,repo):
            items=[x for x in scan_sources(path,root) if source_allowed(root,repo,x.path,defaults)];sources.extend(items)
            for item in items: source_repo[item.source_path]=repo["name"]
    constants=ConstantResolver(sources)
    with sqlite3.connect(a.db) as connection: types=DbTypeIndex(connection)
    facts=[build_fact(source,call,constants,types) for source in sources for call in find_registration_calls(source)]
    writer=GraphWriter(a.db)
    try:
        for fact in facts: import_fact(writer,fact,types)
    finally: writer.close()
    summary=defaultdict(int)
    for fact in facts: summary[fact.api]+=1;summary[f"status:{fact.resolution_status}"]+=1
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps({"summary":dict(sorted(summary.items())),"registrations":[{"registration_id":f.registration_id,"api":f.api,"resolved_key":f.resolved_key,"resolved_instance_type":f.resolved_instance_type,"resolution_status":f.resolution_status,"source_path":f.source_path,"repository":source_repo.get(f.source_path),"line":f.line} for f in facts]},indent=2),encoding="utf-8")
    print(f"Service registrations: {len(facts)}; resolved: {sum(f.resolution_status=='resolved' for f in facts)}")
    return 0
if __name__=="__main__": raise SystemExit(main())
PY

log "Writing TOML configuration"
cat > config/source_roots.toml <<EOF
[workspace]
aosp_root = "$AOSP_ROOT"
auto_discover_manifest = true
auto_enable_discovered = false
strict = false

[defaults]
exclude = [".git", ".repo", "out", "out_*", "target", "dist", "node_modules", "__pycache__", "tests", "test", "testing", "benchmarks"]

[repositories."frameworks/base"]
enabled = true
include = ["core", "services", "packages"]

# Enable additional repo projects here. Manifest projects are discovered but
# intentionally disabled until explicitly enabled to avoid scanning all AOSP.
# [repositories."packages/modules/Permission"]
# enabled = true
# languages = ["java", "kotlin", "aidl"]
EOF

cat > config/parser_registry.toml <<'EOF'
[parsers.java]
implementation = "java_symbol_importer"
enabled = true
capabilities = ["symbols", "inheritance", "service_registration", "permission_enforcement"]

[parsers.aidl]
implementation = "aidl_binder_importer"
enabled = true
capabilities = ["symbols", "binder"]

[parsers.kotlin]
implementation = ""
enabled = false
capabilities = []
[parsers.c]
implementation = ""
enabled = false
capabilities = []
[parsers.cpp]
implementation = ""
enabled = false
capabilities = []
[parsers.rust]
implementation = ""
enabled = false
capabilities = []
[parsers.hidl]
implementation = ""
enabled = false
capabilities = []
[parsers.python]
implementation = ""
enabled = false
capabilities = []
[parsers.blueprint]
implementation = ""
enabled = false
capabilities = []
[parsers.make]
implementation = ""
enabled = false
capabilities = []
[parsers.proto]
implementation = ""
enabled = false
capabilities = []
EOF

cat > scripts/rebuild_all.sh <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONFIG="$PROJECT_ROOT/config/source_roots.toml"
REGISTRY="$PROJECT_ROOT/config/parser_registry.toml"
WORKSPACE_DIR="$PROJECT_ROOT/data/workspace"
PLAN="$WORKSPACE_DIR/execution-plan.json"
DB="$PROJECT_ROOT/data/android_context.db"
MODE="rebuild"; STRICT=();
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-config) SOURCE_CONFIG="$2"; shift 2;;
    --discover-only) MODE="discover"; shift;;
    --plan-only) MODE="plan"; shift;;
    --strict) STRICT+=(--strict); shift;;
    --strict-capability) STRICT+=(--strict-capability "$2"); shift 2;;
    -h|--help) echo "Usage: $0 [--source-config FILE] [--discover-only|--plan-only] [--strict] [--strict-capability NAME]"; exit 0;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done
cd "$PROJECT_ROOT"; source .venv/bin/activate; export PYTHONPATH="$PROJECT_ROOT"
python -m workspace.cli --config "$SOURCE_CONFIG" --registry "$REGISTRY" --out-dir "$WORKSPACE_DIR" "${STRICT[@]}"
[[ "$MODE" == "discover" || "$MODE" == "plan" ]] && exit 0
rm -f "$DB"; sqlite3 "$DB" < storage/schema.sql
rm -rf data/raw/ctags data/raw/aidl data/raw/inheritance data/raw/service
mkdir -p data/raw/ctags data/raw/aidl data/raw/inheritance data/raw/service
python -m workspace.pipeline java --plan "$PLAN" --db "$DB" --ctags-dir data/raw/ctags
python -m workspace.multi_aidl --plan "$PLAN" --db "$DB" --report data/raw/aidl/aidl-binder-report.json
python -m workspace.pipeline inheritance --plan "$PLAN" --db "$DB" --ctags-dir data/raw/ctags --report-dir data/raw/inheritance
python -m workspace.multi_service --plan "$PLAN" --db "$DB" --report data/raw/service/service-registration-report.json
python -m workspace.pipeline annotate --plan "$PLAN" --db "$DB"
if [[ -n "$(sqlite3 "$DB" 'PRAGMA foreign_key_check;')" ]]; then echo "foreign_key_check: FAIL" >&2; exit 5; fi
echo "foreign_key_check: PASS"
[[ -f queries/ams_service_chain.sql ]] && sqlite3 -header -column "$DB" < queries/ams_service_chain.sql
[[ -f queries/pms_service_chain.sql ]] && sqlite3 -header -column "$DB" < queries/pms_service_chain.sql
echo "Workspace coverage:"
python - <<'PY'
import json
from collections import Counter
from pathlib import Path
items=json.loads(Path('data/workspace/capability-report.json').read_text())
for key,value in sorted(Counter(x['status'] for x in items).items()): print(f'  {key}: {value}')
PY
SH
chmod +x scripts/rebuild_all.sh

cat > queries/workspace_coverage_summary.sql <<'SQL'
SELECT json_extract(properties_json, '$.repository') AS repository,
       node_type, COUNT(*) AS count
FROM node
WHERE json_extract(properties_json, '$.repository') IS NOT NULL
GROUP BY repository, node_type
ORDER BY repository, count DESC;
SQL

log "Running GREEN unit tests"
python -m pytest -q tests/unit/test_workspace_v01.py

log "Running two-repository graph integration test"
python -m pytest -q tests/integration/test_multi_repository_pipeline.py

log "Testing non-strict and strict planner behavior"
FIXTURE="$(mktemp -d)"; trap 'rm -rf "$FIXTURE"' EXIT
mkdir -p "$FIXTURE/aosp/demo/repo" "$FIXTURE/out"
printf 'class Demo {}\n' > "$FIXTURE/aosp/demo/repo/Demo.java"
printf 'class KotlinDemo\n' > "$FIXTURE/aosp/demo/repo/Demo.kt"
cat > "$FIXTURE/source.toml" <<EOF
[workspace]
aosp_root = "$FIXTURE/aosp"
auto_discover_manifest = false
[repositories."demo/repo"]
enabled = true
EOF
python -m workspace.cli --config "$FIXTURE/source.toml" --registry config/parser_registry.toml --out-dir "$FIXTURE/out"
if python -m workspace.cli --config "$FIXTURE/source.toml" --registry config/parser_registry.toml --out-dir "$FIXTURE/out-strict" --strict; then
    die "strict fixture unexpectedly passed"
else
    log "Strict fixture failed as expected"
fi

log "Syntax checking canonical scripts"
bash -n scripts/rebuild_all.sh
python -m py_compile workspace/*.py

log "Running full multi-repository rebuild"
./scripts/rebuild_all.sh

log "Updating documentation"
cat >> README.md <<'EOF'

## Multi-Repository Source Configuration v0.1

Repository discovery, language inventory, parser coverage and graph execution are configured in `config/source_roots.toml`. Repo manifest projects are discovered but disabled by default; explicitly enable repositories to control scan size.

```bash
./scripts/rebuild_all.sh --discover-only
./scripts/rebuild_all.sh --plan-only
./scripts/rebuild_all.sh
./scripts/rebuild_all.sh --strict
./scripts/rebuild_all.sh --strict-capability permission_enforcement
```

Java Symbol, AIDL/Binder, Java Inheritance and Java Service Registration support multiple enabled repositories. Kotlin, C/C++, Rust and HIDL are inventoried and reported as unsupported until a capability-specific parser is registered.
EOF
cat >> INSTALLATION_MANIFEST.txt <<'EOF'

Multi-Repository Source Configuration v0.1
  workspace/{models,config,manifest,languages,registry,planner,cli,pipeline,multi_aidl,multi_service}.py
  config/source_roots.toml
  config/parser_registry.toml
  tests/unit/test_workspace_v01.py
  queries/workspace_coverage_summary.sql
  scripts/rebuild_all.sh
EOF

log "Multi-Repository Source Configuration v0.1 completed"
echo "Canonical rebuild: cd $PROJECT_ROOT && ./scripts/rebuild_all.sh"
echo "Enable additional repositories in: $PROJECT_ROOT/config/source_roots.toml"
