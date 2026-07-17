#!/usr/bin/env bash
set -Eeuo pipefail

AOSP_ROOT="${AOSP_ROOT:-/home/ts/aosp}"
PROJECT_ROOT="${PROJECT_ROOT:-/home/ts/android-context-intelligence}"
DB_PATH="$PROJECT_ROOT/data/android_context.db"
STAMP="$(date +%Y%m%d-%H%M%S)"

log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf '\n[ERROR] %s\n' "$*" >&2; exit 1; }
trap 'die "failed at line $LINENO"' ERR

for command in python3 ctags sqlite3 flock; do
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


def run_kotlin(plan: dict, db: Path, raw_dir: Path) -> list[dict]:
    aosp = Path(plan["aosp_root"]); raw_dir.mkdir(parents=True, exist_ok=True)
    duplicates: list[dict] = []
    for repo in repositories_for(plan, "kotlin", "symbols"):
        output = raw_dir / f"{slug(repo['name'])}-kotlin.jsonl"
        command = ["ctags", "--languages=Kotlin", "--output-format=json", "--fields=+nKSEi", "-R", "-f", str(output)]
        for pattern in list(plan.get("default_exclude", [])) + list(repo.get("exclude", [])):
            command.append(f"--exclude={pattern}")
        command.extend(str(x) for x in scan_paths(aosp, repo))
        subprocess.run(command, check=True)
        before = node_sources(db)
        subprocess.run([sys.executable, "-m", "collectors.source.ctags_importer", str(output), str(db), str(aosp), "--language", "kotlin"], check=True)
        after = node_sources(db)
        for node_id, old_path in before.items():
            new_path = after.get(node_id)
            if old_path and new_path and old_path != new_path:
                duplicates.append({"node_id": node_id, "first_source": old_path, "replacement_source": new_path, "repository": repo["name"]})
    (raw_dir / "duplicate-qualified-names-kotlin.json").write_text(json.dumps(duplicates, indent=2), encoding="utf-8")
    return duplicates


def run_inheritance(plan_path: Path, plan: dict, db: Path, raw_dir: Path, report_dir: Path) -> None:
    aosp = Path(plan["aosp_root"]); report_dir.mkdir(parents=True, exist_ok=True)
    for lang in ["java", "kotlin"]:
        for repo in repositories_for(plan, lang, "inheritance"):
            suffix = "-kotlin" if lang == "kotlin" else ""
            source = raw_dir / f"{slug(repo['name'])}{suffix}.jsonl"
            if source.is_file():
                subprocess.run([sys.executable, "-m", "collectors.source.java_inheritance_importer",
                    "--ctags-jsonl", str(source), "--source-root", str(aosp), "--db", str(db),
                    "--report", str(report_dir / f"{slug(repo['name'])}{suffix}.json")], check=True)


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
    p = argparse.ArgumentParser(); p.add_argument("command", choices=["java", "kotlin", "inheritance", "annotate"])
    p.add_argument("--plan", type=Path, required=True); p.add_argument("--db", type=Path, required=True)
    p.add_argument("--ctags-dir", type=Path, default=Path("data/raw/ctags")); p.add_argument("--report-dir", type=Path, default=Path("data/raw/inheritance"))
    a = p.parse_args(); plan = load_plan(a.plan)
    if a.command == "java": run_java(plan, a.db, a.ctags_dir)
    elif a.command == "kotlin": run_kotlin(plan, a.db, a.ctags_dir)
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

cat > workspace/build_publish.py <<'PY_BUILD_PUBLISH'
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from graph.writer import GraphWriter, Node


RAW_REPORT_DIRECTORIES = ("ctags", "aidl", "inheritance", "service")


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildBatch:
    data_root: Path
    build_id: str
    staging_root: Path
    database: Path
    workspace: Path
    raw: Path
    rollback_root: Path
    journal: Path


def generate_build_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{os.getpid()}-{secrets.token_hex(4)}"


def _validate_build_id(build_id: str) -> str:
    if (
        not build_id
        or build_id in {".", ".."}
        or ".." in build_id
        or "/" in build_id
        or "\\" in build_id
    ):
        raise ValueError(f"unsafe build ID: {build_id!r}")
    return build_id


def _batch_from_parts(data_root: Path, build_id: str) -> BuildBatch:
    staging_root = data_root / "staging" / build_id
    return BuildBatch(
        data_root=data_root,
        build_id=build_id,
        staging_root=staging_root,
        database=staging_root / "android_context.db",
        workspace=staging_root / "workspace",
        raw=staging_root / "raw",
        rollback_root=data_root / "rollback" / build_id,
        journal=data_root / ".publish-journal.json",
    )


def begin_build(data_root: Path, build_id: str | None = None) -> BuildBatch:
    resolved_root = data_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    identity = _validate_build_id(build_id or generate_build_id())
    batch = _batch_from_parts(resolved_root, identity)
    batch.staging_root.mkdir(parents=True, exist_ok=False)
    batch.workspace.mkdir()
    batch.raw.mkdir()
    for name in RAW_REPORT_DIRECTORIES:
        (batch.raw / name).mkdir()
    return batch


def load_build_batch(staging_root: Path) -> BuildBatch:
    resolved_staging = staging_root.resolve()
    if resolved_staging.parent.name != "staging":
        raise ValueError(f"staging path must be under data/staging: {staging_root}")
    build_id = _validate_build_id(resolved_staging.name)
    return _batch_from_parts(resolved_staging.parent.parent, build_id)


def cleanup_failed_build(batch: BuildBatch, keep: bool) -> Path | None:
    if keep:
        return batch.staging_root.resolve() if batch.staging_root.exists() else None
    if batch.staging_root.exists():
        shutil.rmtree(batch.staging_root)
    return None


def record_graph_build(
    batch: BuildBatch,
    source_config: Path,
    started_at: str,
    verified_at: str,
) -> None:
    writer = GraphWriter(batch.database)
    try:
        writer.upsert_node(
            Node(
                node_id=f"GRAPH_BUILD:{batch.build_id}",
                node_type="GRAPH_BUILD",
                qualified_name=batch.build_id,
                display_name=batch.build_id,
                properties={
                    "source_config": str(source_config.resolve()),
                    "started_at": started_at,
                    "verified_at": verified_at,
                },
                extractor="build_publish",
            )
        )
    finally:
        writer.close()


def write_build_manifest(
    batch: BuildBatch,
    source_config: Path,
    started_at: str,
    verified_at: str,
) -> Path:
    manifest = batch.workspace / "build-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "build_id": batch.build_id,
                "source_config": str(source_config.resolve()),
                "started_at": started_at,
                "status": "verified",
                "verified_at": verified_at,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def read_graph_build_id(database: Path) -> str | None:
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(database)
        row = connection.execute(
            """
            SELECT qualified_name
            FROM node
            WHERE node_type = 'GRAPH_BUILD'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if "connection" in locals():
            connection.close()
    return str(row[0]) if row and row[0] is not None else None


def _database_sidecars(database: Path) -> tuple[Path, Path]:
    return Path(f"{database}-wal"), Path(f"{database}-shm")


def _assert_no_sidecars(database: Path) -> None:
    existing = [str(path) for path in _database_sidecars(database) if path.exists()]
    if existing:
        raise PublicationError(
            "SQLite sidecar files prevent publication: " + ", ".join(existing)
        )


def prepare_staged_database(database: Path) -> None:
    if not database.is_file():
        raise PublicationError(f"staged database does not exist: {database}")
    connection = sqlite3.connect(database, timeout=0.2)
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise PublicationError(f"staged database WAL is busy: {checkpoint}")
        mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if not mode or str(mode[0]).lower() != "delete":
            raise PublicationError(f"failed to disable staged WAL: {mode}")
    finally:
        connection.close()
    _assert_no_sidecars(database)


def ensure_live_database_quiescent(database: Path) -> None:
    if not database.exists():
        return
    try:
        connection = sqlite3.connect(database, timeout=0.2)
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0]) != 0:
            raise PublicationError(f"live database WAL is busy: {checkpoint}")
    except sqlite3.Error as error:
        raise PublicationError(f"cannot checkpoint live database: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()
    _assert_no_sidecars(database)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_publication_journal(batch: BuildBatch, phase: str) -> None:
    payload = {
        "build_id": batch.build_id,
        "staging_root": str(batch.staging_root),
        "rollback_root": str(batch.rollback_root),
        "phase": phase,
    }
    batch.journal.parent.mkdir(parents=True, exist_ok=True)
    temporary = batch.journal.with_name(
        f"{batch.journal.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    )
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, batch.journal)
        _fsync_directory(batch.journal.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _read_verified_manifest_build_id(batch: BuildBatch) -> str | None:
    manifest = batch.workspace / "build-manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("status") != "verified":
        return None
    identity = payload.get("build_id")
    return str(identity) if identity is not None else None


def _validate_batch_identity(batch: BuildBatch) -> None:
    database_build_id = read_graph_build_id(batch.database)
    manifest_build_id = _read_verified_manifest_build_id(batch)
    if database_build_id != batch.build_id or manifest_build_id != batch.build_id:
        raise PublicationError(
            "build identity mismatch: "
            f"expected {batch.build_id!r}, database={database_build_id!r}, "
            f"manifest={manifest_build_id!r}"
        )


def _restore_precommit_reports(
    batch: BuildBatch,
    published_names: set[str],
    backed_up_names: set[str],
) -> None:
    batch.staging_root.mkdir(parents=True, exist_ok=True)
    for name in published_names:
        live = batch.data_root / name
        staged = batch.staging_root / name
        if live.exists():
            if staged.exists():
                raise PublicationError(
                    f"cannot restore staged {name}: destination already exists"
                )
            os.replace(live, staged)
    for name in backed_up_names:
        backup = batch.rollback_root / name
        live = batch.data_root / name
        if backup.exists():
            if live.exists():
                raise PublicationError(
                    f"cannot restore live {name}: destination already exists"
                )
            os.replace(backup, live)
    if batch.rollback_root.exists():
        shutil.rmtree(batch.rollback_root)


def publish_build(
    batch: BuildBatch,
    *,
    replace_database: Callable[[Path, Path], None] = os.replace,
) -> None:
    _validate_batch_identity(batch)
    _assert_no_sidecars(batch.database)
    ensure_live_database_quiescent(batch.data_root / "android_context.db")
    if batch.journal.exists():
        raise PublicationError(
            f"publication journal already exists: {batch.journal}; recover first"
        )

    backed_up_names: set[str] = set()
    published_names: set[str] = set()
    database_committed = False
    _write_publication_journal(batch, "prepared")
    try:
        batch.rollback_root.mkdir(parents=True, exist_ok=False)
        for name in ("workspace", "raw"):
            live = batch.data_root / name
            if live.exists():
                os.replace(live, batch.rollback_root / name)
                backed_up_names.add(name)
        _write_publication_journal(batch, "old_reports_backed_up")

        for name in ("workspace", "raw"):
            staged = batch.staging_root / name
            if not staged.is_dir():
                raise PublicationError(f"staged report directory is missing: {staged}")
            os.replace(staged, batch.data_root / name)
            published_names.add(name)
        _write_publication_journal(batch, "new_reports_published")

        replace_database(batch.database, batch.data_root / "android_context.db")
        database_committed = True
        _write_publication_journal(batch, "database_committed")
    except BaseException:
        if not database_committed:
            _restore_precommit_reports(batch, published_names, backed_up_names)
            if batch.journal.exists():
                batch.journal.unlink()
                _fsync_directory(batch.journal.parent)
        raise

    if batch.rollback_root.exists():
        shutil.rmtree(batch.rollback_root)
    if batch.staging_root.exists():
        shutil.rmtree(batch.staging_root)
    if batch.journal.exists():
        batch.journal.unlink()
        _fsync_directory(batch.journal.parent)


def _load_journal_batch(data_root: Path, payload: dict[str, object]) -> BuildBatch:
    identity = _validate_build_id(str(payload.get("build_id", "")))
    batch = _batch_from_parts(data_root, identity)
    try:
        staging = Path(str(payload["staging_root"])).resolve()
        rollback = Path(str(payload["rollback_root"])).resolve()
    except (KeyError, OSError) as error:
        raise PublicationError("invalid publication journal paths") from error
    if staging != batch.staging_root.resolve() or rollback != batch.rollback_root.resolve():
        raise PublicationError("publication journal paths do not match build identity")
    return batch


def recover_publication(data_root: Path) -> str:
    resolved_root = data_root.resolve()
    journal = resolved_root / ".publish-journal.json"
    if not journal.exists():
        return "no_journal"
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicationError(f"invalid publication journal: {journal}") from error
    if not isinstance(payload, dict):
        raise PublicationError(f"invalid publication journal: {journal}")
    batch = _load_journal_batch(resolved_root, payload)

    live_database = resolved_root / "android_context.db"
    if read_graph_build_id(live_database) == batch.build_id:
        if batch.rollback_root.exists():
            shutil.rmtree(batch.rollback_root)
        if batch.staging_root.exists():
            shutil.rmtree(batch.staging_root)
        journal.unlink()
        _fsync_directory(journal.parent)
        return "committed"

    batch.staging_root.mkdir(parents=True, exist_ok=True)
    for name in ("workspace", "raw"):
        live = resolved_root / name
        staged = batch.staging_root / name
        backup = batch.rollback_root / name
        if live.exists() and not staged.exists():
            os.replace(live, staged)
        if backup.exists():
            if live.exists():
                raise PublicationError(
                    f"cannot recover {name}: live and rollback paths both exist"
                )
            os.replace(backup, live)
    if batch.rollback_root.exists():
        shutil.rmtree(batch.rollback_root)
    journal.unlink()
    _fsync_directory(journal.parent)
    return "rolled_back"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish verified graph build batches")
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("begin", help="create a staged build batch")
    begin.add_argument("--data-root", type=Path, required=True)

    prepare = commands.add_parser("prepare", help="identify and prepare a batch")
    prepare.add_argument("--staging", type=Path, required=True)
    prepare.add_argument("--source-config", type=Path, required=True)
    prepare.add_argument("--started-at", required=True)
    prepare.add_argument("--verified-at", required=True)

    publish = commands.add_parser("publish", help="publish a verified batch")
    publish.add_argument("--staging", type=Path, required=True)

    fail = commands.add_parser("fail", help="clean up or retain a failed batch")
    fail.add_argument("--staging", type=Path, required=True)
    fail.add_argument("--keep", action="store_true")

    recover = commands.add_parser("recover", help="recover interrupted publication")
    recover.add_argument("--data-root", type=Path, required=True)
    return parser


def main(argument_vector: list[str] | None = None) -> int:
    arguments = _build_argument_parser().parse_args(argument_vector)
    if arguments.command == "begin":
        print(begin_build(arguments.data_root).staging_root.resolve())
        return 0
    if arguments.command == "prepare":
        batch = load_build_batch(arguments.staging)
        record_graph_build(
            batch,
            arguments.source_config,
            arguments.started_at,
            arguments.verified_at,
        )
        write_build_manifest(
            batch,
            arguments.source_config,
            arguments.started_at,
            arguments.verified_at,
        )
        prepare_staged_database(batch.database)
        return 0
    if arguments.command == "publish":
        publish_build(load_build_batch(arguments.staging))
        return 0
    if arguments.command == "fail":
        retained = cleanup_failed_build(
            load_build_batch(arguments.staging),
            keep=arguments.keep,
        )
        if retained is not None:
            print(retained)
        return 0
    if arguments.command == "recover":
        print(recover_publication(arguments.data_root))
        return 0
    raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
PY_BUILD_PUBLISH

cat > tests/unit/test_build_publish.py <<'PY_BUILD_PUBLISH_TEST'
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from graph.writer import GraphWriter, Node
from workspace.build_publish import (
    PublicationError,
    begin_build,
    cleanup_failed_build,
    ensure_live_database_quiescent,
    load_build_batch,
    main,
    prepare_staged_database,
    publish_build,
    read_graph_build_id,
    record_graph_build,
    recover_publication,
    write_build_manifest,
)


def create_full_node_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE node (
          node_id TEXT PRIMARY KEY,
          node_type TEXT NOT NULL,
          qualified_name TEXT,
          display_name TEXT NOT NULL,
          properties_json TEXT NOT NULL DEFAULT '{}',
          source_path TEXT,
          line_start INTEGER,
          line_end INTEGER,
          source_revision TEXT,
          extractor TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          content_hash TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          updated_at TEXT NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()


def test_begin_build_creates_isolated_batch(tmp_path: Path) -> None:
    live_db = tmp_path / "android_context.db"
    live_db.write_bytes(b"verified")

    batch = begin_build(tmp_path, build_id="build-1")

    assert batch.staging_root == tmp_path / "staging" / "build-1"
    assert batch.database == batch.staging_root / "android_context.db"
    assert batch.workspace.is_dir()
    assert batch.raw.is_dir()
    assert (batch.raw / "ctags").is_dir()
    assert (batch.raw / "aidl").is_dir()
    assert (batch.raw / "inheritance").is_dir()
    assert (batch.raw / "service").is_dir()
    assert live_db.read_bytes() == b"verified"


def test_load_build_batch_reconstructs_paths(tmp_path: Path) -> None:
    created = begin_build(tmp_path, build_id="build-1")

    loaded = load_build_batch(created.staging_root)

    assert loaded == created


@pytest.mark.parametrize("build_id", ["../escape", "nested/name", "nested\\name", ".."])
def test_begin_build_rejects_unsafe_build_id(tmp_path: Path, build_id: str) -> None:
    with pytest.raises(ValueError, match="build ID"):
        begin_build(tmp_path, build_id=build_id)


def test_failed_build_is_deleted_by_default_and_cleanup_is_idempotent(
    tmp_path: Path,
) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    batch.database.write_bytes(b"partial")

    retained = cleanup_failed_build(batch, keep=False)

    assert retained is None
    assert not batch.staging_root.exists()
    assert cleanup_failed_build(batch, keep=False) is None


def test_keep_failed_build_preserves_complete_batch(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    batch.database.write_bytes(b"partial")
    (batch.workspace / "report.json").write_text("{}", encoding="utf-8")

    retained = cleanup_failed_build(batch, keep=True)

    assert retained == batch.staging_root.resolve()
    assert batch.database.read_bytes() == b"partial"
    assert (batch.workspace / "report.json").read_text(encoding="utf-8") == "{}"


def test_records_matching_database_and_manifest_build_ids(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    source_config = tmp_path / "source_roots.toml"
    source_config.write_text("[workspace]\n", encoding="utf-8")
    create_full_node_schema(batch.database)

    record_graph_build(
        batch,
        source_config,
        "2026-07-16T15:00:00Z",
        "2026-07-16T15:01:00Z",
    )
    write_build_manifest(
        batch,
        source_config,
        "2026-07-16T15:00:00Z",
        "2026-07-16T15:01:00Z",
    )

    assert read_graph_build_id(batch.database) == batch.build_id
    manifest = json.loads(
        (batch.workspace / "build-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "build_id": "build-1",
        "source_config": str(source_config.resolve()),
        "started_at": "2026-07-16T15:00:00Z",
        "status": "verified",
        "verified_at": "2026-07-16T15:01:00Z",
    }


def test_prepare_staged_database_removes_wal_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "staged.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sample(value TEXT)")
    connection.execute("INSERT INTO sample VALUES('value')")
    connection.commit()
    connection.close()

    prepare_staged_database(database)

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    connection.close()
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def test_busy_live_database_rejects_publication(tmp_path: Path) -> None:
    database = tmp_path / "android_context.db"
    active = sqlite3.connect(database)
    active.execute("PRAGMA journal_mode=WAL")
    active.execute("CREATE TABLE sample(value TEXT)")
    active.execute("INSERT INTO sample VALUES('active')")
    active.commit()
    sidecar = Path(f"{database}-wal")
    assert sidecar.exists()

    try:
        with pytest.raises(PublicationError, match="sidecar"):
            ensure_live_database_quiescent(database)
        assert sidecar.exists()
    finally:
        active.close()


def test_missing_live_database_is_quiescent(tmp_path: Path) -> None:
    ensure_live_database_quiescent(tmp_path / "missing.db")


def seed_database(path: Path, build_id: str) -> None:
    create_full_node_schema(path)
    writer = GraphWriter(path)
    writer.upsert_node(
        Node(
            node_id=f"GRAPH_BUILD:{build_id}",
            node_type="GRAPH_BUILD",
            qualified_name=build_id,
            display_name=build_id,
            extractor="test",
        )
    )
    writer.close()


def seed_reports(root: Path, marker: str) -> None:
    for name in ("workspace", "raw"):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "marker.txt").write_text(marker, encoding="utf-8")


def ready_batch(data_root: Path, build_id: str = "new"):
    batch = begin_build(data_root, build_id=build_id)
    seed_database(batch.database, build_id)
    (batch.workspace / "marker.txt").write_text("new", encoding="utf-8")
    (batch.workspace / "build-manifest.json").write_text(
        json.dumps({"build_id": build_id, "status": "verified"}),
        encoding="utf-8",
    )
    (batch.raw / "marker.txt").write_text("new", encoding="utf-8")
    return batch


def test_publish_replaces_reports_and_database_as_one_batch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)

    publish_build(batch)

    assert read_graph_build_id(data / "android_context.db") == "new"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "new"
    assert (data / "raw/marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.journal.exists()
    assert not batch.rollback_root.exists()
    assert not batch.staging_root.exists()


def test_precommit_failure_restores_old_batch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    before = hashlib.sha256((data / "android_context.db").read_bytes()).hexdigest()
    batch = ready_batch(data)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected database replacement failure")

    with pytest.raises(OSError, match="injected"):
        publish_build(batch, replace_database=fail_replace)

    after = hashlib.sha256((data / "android_context.db").read_bytes()).hexdigest()
    assert after == before
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "old"
    assert (batch.workspace / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.journal.exists()


def test_publish_rejects_mismatched_identity_before_moving_live_files(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    (batch.workspace / "build-manifest.json").write_text(
        json.dumps({"build_id": "different", "status": "verified"}),
        encoding="utf-8",
    )

    with pytest.raises(PublicationError, match="build identity"):
        publish_build(batch)

    assert read_graph_build_id(data / "android_context.db") == "old"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "old"


def simulate_reports_published(batch) -> None:
    batch.rollback_root.mkdir(parents=True)
    os.replace(batch.data_root / "workspace", batch.rollback_root / "workspace")
    os.replace(batch.data_root / "raw", batch.rollback_root / "raw")
    os.replace(batch.workspace, batch.data_root / "workspace")
    os.replace(batch.raw, batch.data_root / "raw")
    batch.journal.write_text(
        json.dumps(
            {
                "build_id": batch.build_id,
                "staging_root": str(batch.staging_root),
                "rollback_root": str(batch.rollback_root),
                "phase": "new_reports_published",
            }
        ),
        encoding="utf-8",
    )


def test_recovery_rolls_back_when_database_has_old_build_id(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    simulate_reports_published(batch)

    assert recover_publication(data) == "rolled_back"
    assert read_graph_build_id(data / "android_context.db") == "old"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "old"
    assert (batch.workspace / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.journal.exists()


def test_recovery_finishes_cleanup_when_database_has_new_build_id(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    simulate_reports_published(batch)
    os.replace(batch.database, data / "android_context.db")

    assert recover_publication(data) == "committed"
    assert read_graph_build_id(data / "android_context.db") == "new"
    assert (data / "workspace/marker.txt").read_text(encoding="utf-8") == "new"
    assert not batch.rollback_root.exists()
    assert not batch.journal.exists()
    assert recover_publication(data) == "no_journal"


def test_first_build_failure_leaves_live_database_absent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    batch = ready_batch(data)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected first-build failure")

    with pytest.raises(OSError, match="first-build"):
        publish_build(batch, replace_database=fail_replace)

    assert not (data / "android_context.db").exists()
    assert batch.database.exists()


def test_cli_begin_prints_only_staging_path(tmp_path: Path, capsys) -> None:
    assert main(["begin", "--data-root", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    staging = Path(captured.out.strip())
    assert captured.err == ""
    assert staging.parent == tmp_path.resolve() / "staging"
    assert staging.is_dir()


def test_cli_fail_keep_prints_retained_path(tmp_path: Path, capsys) -> None:
    batch = begin_build(tmp_path, build_id="build-1")

    assert main(["fail", "--staging", str(batch.staging_root), "--keep"]) == 0

    assert Path(capsys.readouterr().out.strip()) == batch.staging_root.resolve()


def test_cli_recover_without_journal_is_success(tmp_path: Path, capsys) -> None:
    assert main(["recover", "--data-root", str(tmp_path)]) == 0

    assert capsys.readouterr().out.strip() == "no_journal"


def test_cli_prepare_records_and_checkpoints_batch(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    create_full_node_schema(batch.database)
    source_config = tmp_path / "source_roots.toml"
    source_config.write_text("[workspace]\n", encoding="utf-8")

    assert main(
        [
            "prepare",
            "--staging",
            str(batch.staging_root),
            "--source-config",
            str(source_config),
            "--started-at",
            "2026-07-16T15:00:00Z",
            "--verified-at",
            "2026-07-16T15:01:00Z",
        ]
    ) == 0

    assert read_graph_build_id(batch.database) == "build-1"
    manifest = json.loads(
        (batch.workspace / "build-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["build_id"] == "build-1"


def test_cli_publish_commits_ready_batch(tmp_path: Path) -> None:
    batch = ready_batch(tmp_path, build_id="build-1")

    assert main(["publish", "--staging", str(batch.staging_root)]) == 0

    assert read_graph_build_id(tmp_path / "android_context.db") == "build-1"
PY_BUILD_PUBLISH_TEST

cat > tests/integration/test_atomic_rebuild.py <<'PY_ATOMIC_REBUILD_TEST'
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest


SNAPSHOT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCRIPT = SNAPSHOT_ROOT / "scripts" / "rebuild_all.sh"
BASH = shutil.which("bash")
FLOCK = shutil.which("flock")


def test_canonical_rebuild_declares_atomic_staging_contract() -> None:
    script = CANONICAL_SCRIPT.read_text(encoding="utf-8")

    assert "--keep-failed-db" in script
    assert 'flock -n 9' in script
    assert "workspace.build_publish recover" in script
    assert "workspace.build_publish begin" in script
    assert "workspace.build_publish prepare" in script
    assert "workspace.build_publish publish" in script
    assert 'STAGED_DB="$STAGING/android_context.db"' in script
    assert 'STAGED_WORKSPACE="$STAGING/workspace"' in script
    assert 'STAGED_RAW="$STAGING/raw"' in script


SCHEMA = """
CREATE TABLE node (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  qualified_name TEXT,
  display_name TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL
);
CREATE TABLE edge (
  edge_id TEXT PRIMARY KEY,
  edge_type TEXT NOT NULL,
  from_node_id TEXT NOT NULL,
  to_node_id TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL,
  FOREIGN KEY(from_node_id) REFERENCES node(node_id),
  FOREIGN KEY(to_node_id) REFERENCES node(node_id)
);
"""


CLI_STUB = r'''from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--registry", required=True)
parser.add_argument("--out-dir", type=Path, required=True)
parser.add_argument("--strict", action="store_true")
parser.add_argument("--strict-capability")
args = parser.parse_args()
args.out_dir.mkdir(parents=True, exist_ok=True)
(args.out_dir / "execution-plan.json").write_text("{}\n", encoding="utf-8")
(args.out_dir / "capability-report.json").write_text(
    json.dumps([{"status": "scheduled"}]) + "\n", encoding="utf-8"
)
(args.out_dir / "marker.txt").write_text("new", encoding="utf-8")
'''


PIPELINE_STUB = r'''from __future__ import annotations
import argparse
import os
from pathlib import Path
from graph.writer import Edge, GraphWriter, Node

parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--plan")
parser.add_argument("--db", type=Path, required=True)
parser.add_argument("--ctags-dir", type=Path)
parser.add_argument("--report-dir", type=Path)
args = parser.parse_args()
if args.command == "java" and os.environ.get("FORCE_IMPORTER_FAILURE") == "1":
    raise SystemExit(17)
if args.ctags_dir:
    args.ctags_dir.mkdir(parents=True, exist_ok=True)
    (args.ctags_dir / "marker.txt").write_text("new", encoding="utf-8")
if args.command == "annotate":
    writer = GraphWriter(args.db)
    writer.upsert_node(Node(
        node_id="JAVA_CLASS:fixture.LocalService",
        node_type="JAVA_CLASS",
        qualified_name="fixture.LocalService",
        display_name="LocalService",
        extractor="fixture",
    ))
    writer.upsert_node(Node(
        node_id="LOCAL_SERVICE_KEY:fixture.LocalKey",
        node_type="LOCAL_SERVICE_KEY",
        qualified_name="fixture.LocalKey",
        display_name="LocalKey",
        extractor="fixture",
    ))
    writer.upsert_edge(Edge(
        edge_type="EXPOSED_AS_LOCAL_SERVICE",
        from_node_id="JAVA_CLASS:fixture.LocalService",
        to_node_id="LOCAL_SERVICE_KEY:fixture.LocalKey",
        extractor="fixture",
    ))
    writer.close()
'''


REPORT_STUB = r'''from __future__ import annotations
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--plan")
parser.add_argument("--db")
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text("{}\n", encoding="utf-8")
'''


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_database(path: Path, build_id: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute(
        """
        INSERT INTO node (
            node_id, node_type, qualified_name, display_name, properties_json,
            source_revision, extractor, extractor_version, content_hash,
            status, updated_at
        ) VALUES (?, 'GRAPH_BUILD', ?, ?, '{}', 'fixture', 'fixture', '1', '',
                  'active', '2026-07-16T00:00:00Z')
        """,
        (f"GRAPH_BUILD:{build_id}", build_id, build_id),
    )
    connection.commit()
    connection.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    if BASH is None or FLOCK is None:
        pytest.skip("atomic rebuild integration requires bash and flock")
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(SNAPSHOT_ROOT / "workspace", root / "workspace")
    shutil.copytree(SNAPSHOT_ROOT / "graph", root / "graph")
    (root / "scripts").mkdir()
    shutil.copy2(CANONICAL_SCRIPT, root / "scripts" / "rebuild_all.sh")
    _write(root / ".venv/bin/activate", "")
    _write(root / "storage/schema.sql", SCHEMA)
    _write(root / "config/source_roots.toml", "[workspace]\n")
    _write(root / "config/parser_registry.toml", "[parsers]\n")
    _write(root / "workspace/cli.py", CLI_STUB)
    _write(root / "workspace/pipeline.py", PIPELINE_STUB)
    _write(root / "workspace/multi_aidl.py", REPORT_STUB)
    _write(root / "workspace/multi_service.py", REPORT_STUB)
    data = root / "data"
    data.mkdir()
    _seed_database(data / "android_context.db", "old")
    for name in ("workspace", "raw"):
        _write(data / name / "marker.txt", "old")
    return root


def _run(project: Path, *arguments: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(environment)
    return subprocess.run(
        [BASH, str(project / "scripts/rebuild_all.sh"), *arguments],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_forced_importer_failure_preserves_live_batch(project: Path) -> None:
    database = project / "data/android_context.db"
    before = _checksum(database)

    result = _run(project, FORCE_IMPORTER_FAILURE="1")

    assert result.returncode != 0
    assert _checksum(database) == before
    assert (project / "data/workspace/marker.txt").read_text() == "old"
    assert (project / "data/raw/marker.txt").read_text() == "old"
    staging = project / "data/staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_keep_failed_retains_and_prints_staging_batch(project: Path) -> None:
    result = _run(project, "--keep-failed-db", FORCE_IMPORTER_FAILURE="1")

    assert result.returncode != 0
    retained = [
        Path(line)
        for line in result.stdout.splitlines()
        if "/data/staging/" in line
    ]
    assert len(retained) == 1
    assert retained[0].is_dir()


def test_plan_only_creates_no_staged_database(project: Path) -> None:
    shutil.rmtree(project / "data/workspace")

    result = _run(project, "--plan-only")

    assert result.returncode == 0, result.stderr
    assert (project / "data/workspace/execution-plan.json").is_file()
    assert not (project / "data/staging").exists()


@pytest.mark.parametrize("arguments", [(), ("--plan-only",)])
def test_common_lock_rejects_concurrent_modes(
    project: Path,
    arguments: tuple[str, ...],
) -> None:
    lock = project / "data/.rebuild.lock"
    holder = subprocess.Popen([FLOCK, "-n", str(lock), "sleep", "5"])
    try:
        time.sleep(0.2)
        result = _run(project, *arguments)
    finally:
        holder.terminate()
        holder.wait(timeout=5)

    assert result.returncode != 0
    assert "another rebuild is already running" in result.stderr
    assert (project / "data/workspace/marker.txt").read_text() == "old"


def test_successful_publication_exposes_matching_build_ids(project: Path) -> None:
    result = _run(project)

    assert result.returncode == 0, result.stderr
    database = project / "data/android_context.db"
    connection = sqlite3.connect(database)
    database_build_id = connection.execute(
        "SELECT qualified_name FROM node WHERE node_type='GRAPH_BUILD'"
    ).fetchone()[0]
    connection.close()
    manifest = json.loads(
        (project / "data/workspace/build-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["build_id"] == database_build_id
    assert (project / "data/workspace/marker.txt").read_text() == "new"
    assert (project / "data/raw/ctags/marker.txt").read_text() == "new"
PY_ATOMIC_REBUILD_TEST

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
implementation = "kotlin_ctags_importer"
enabled = true
capabilities = ["symbols", "inheritance", "service_registration", "permission_enforcement"]
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

cat > scripts/rebuild_all.sh <<'SH_REBUILD'
#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONFIG="$PROJECT_ROOT/config/source_roots.toml"
REGISTRY="$PROJECT_ROOT/config/parser_registry.toml"
MODE="rebuild"
KEEP_FAILED=0
STRICT=()

usage() {
    cat <<'EOF'
Usage: rebuild_all.sh [OPTIONS]

Options:
  --source-config FILE        Use an alternate source-roots configuration.
  --discover-only             Refresh workspace discovery reports only.
  --plan-only                 Refresh the execution plan only.
  --strict                    Fail on every unsupported detected capability.
  --strict-capability NAME    Fail when NAME lacks parser coverage.
  --keep-failed-db            Retain the complete failed staging batch.
  -h, --help                  Show this help.
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-config)
            [[ $# -ge 2 ]] || die "--source-config requires a path"
            SOURCE_CONFIG="$2"
            shift 2
            ;;
        --discover-only)
            MODE="discover"
            shift
            ;;
        --plan-only)
            MODE="plan"
            shift
            ;;
        --strict)
            STRICT+=(--strict)
            shift
            ;;
        --strict-capability)
            [[ $# -ge 2 ]] || die "--strict-capability requires a name"
            STRICT+=(--strict-capability "$2")
            shift 2
            ;;
        --keep-failed-db)
            KEEP_FAILED=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

mkdir -p "$PROJECT_ROOT/data"
exec 9>"$PROJECT_ROOT/data/.rebuild.lock"
flock -n 9 || die "another rebuild is already running"

python -m workspace.build_publish recover \
    --data-root "$PROJECT_ROOT/data"

if [[ "$MODE" == "discover" || "$MODE" == "plan" ]]; then
    python -m workspace.cli \
        --config "$SOURCE_CONFIG" \
        --registry "$REGISTRY" \
        --out-dir "$PROJECT_ROOT/data/workspace" \
        "${STRICT[@]}"
    exit 0
fi

STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
STAGING=""
PUBLISHED=0

cleanup_failed_batch() {
    local status=$?
    trap - EXIT INT TERM
    if [[ "$PUBLISHED" -eq 0 && -n "$STAGING" && -d "$STAGING" ]]; then
        if [[ "$KEEP_FAILED" -eq 1 ]]; then
            python -m workspace.build_publish fail \
                --staging "$STAGING" \
                --keep || true
        else
            python -m workspace.build_publish fail \
                --staging "$STAGING" || true
        fi
    fi
    exit "$status"
}

trap cleanup_failed_batch EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STAGING="$(
    python -m workspace.build_publish begin \
        --data-root "$PROJECT_ROOT/data"
)"
STAGED_DB="$STAGING/android_context.db"
STAGED_WORKSPACE="$STAGING/workspace"
STAGED_RAW="$STAGING/raw"
PLAN="$STAGED_WORKSPACE/execution-plan.json"

python -m workspace.cli \
    --config "$SOURCE_CONFIG" \
    --registry "$REGISTRY" \
    --out-dir "$STAGED_WORKSPACE" \
    "${STRICT[@]}"

sqlite3 "$STAGED_DB" < "$PROJECT_ROOT/storage/schema.sql"

python -m workspace.pipeline java \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --ctags-dir "$STAGED_RAW/ctags"

python -m workspace.pipeline kotlin \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --ctags-dir "$STAGED_RAW/ctags"

python -m workspace.multi_aidl \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --report "$STAGED_RAW/aidl/aidl-binder-report.json"

python -m workspace.pipeline inheritance \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --ctags-dir "$STAGED_RAW/ctags" \
    --report-dir "$STAGED_RAW/inheritance"

python -m workspace.multi_service \
    --plan "$PLAN" \
    --db "$STAGED_DB" \
    --report "$STAGED_RAW/service/service-registration-report.json"

python -m workspace.pipeline annotate \
    --plan "$PLAN" \
    --db "$STAGED_DB"

FK_ERRORS="$(sqlite3 "$STAGED_DB" 'PRAGMA foreign_key_check;')"
if [[ -n "$FK_ERRORS" ]]; then
    printf '%s\n' "$FK_ERRORS" >&2
    die "foreign_key_check failed"
fi
printf 'foreign_key_check: PASS\n'

LOCAL_SERVICE_COUNT="$(
    sqlite3 "$STAGED_DB" \
        "SELECT COUNT(*) FROM edge WHERE edge_type='EXPOSED_AS_LOCAL_SERVICE';"
)"
[[ "$LOCAL_SERVICE_COUNT" -ge 1 ]] || die "LocalServices validation failed"

[[ -f "$PROJECT_ROOT/queries/ams_service_chain.sql" ]] &&
    sqlite3 -header -column "$STAGED_DB" \
        < "$PROJECT_ROOT/queries/ams_service_chain.sql"
[[ -f "$PROJECT_ROOT/queries/pms_service_chain.sql" ]] &&
    sqlite3 -header -column "$STAGED_DB" \
        < "$PROJECT_ROOT/queries/pms_service_chain.sql"

VERIFIED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
python -m workspace.build_publish prepare \
    --staging "$STAGING" \
    --source-config "$SOURCE_CONFIG" \
    --started-at "$STARTED_AT" \
    --verified-at "$VERIFIED_AT"

python -m workspace.build_publish publish \
    --staging "$STAGING"

PUBLISHED=1
trap - EXIT INT TERM

printf 'Workspace coverage:\n'
python - "$PROJECT_ROOT/data/workspace/capability-report.json" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key, value in sorted(Counter(item["status"] for item in items).items()):
    print(f"  {key}: {value}")
PY
SH_REBUILD
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
printf 'class KotlinDemo\n' > "$FIXTURE/aosp/demo/repo/Demo.cpp"
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

log "Running atomic publisher and rebuild integration tests"
python -m pytest -q \
    tests/unit/test_build_publish.py \
    tests/integration/test_atomic_rebuild.py

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

## Atomic Database Rebuild v0.1

The canonical rebuild creates one verified batch under `data/staging/<build-id>`
and atomically replaces `data/android_context.db` only after all reports and
validation gates pass. A pre-commit failure preserves the previous live batch.

```bash
./scripts/rebuild_all.sh
./scripts/rebuild_all.sh --keep-failed-db

sqlite3 data/android_context.db \
  "SELECT qualified_name FROM node WHERE node_type='GRAPH_BUILD';"
jq -r '.build_id' data/workspace/build-manifest.json
```

Failed batches are deleted by default or retained under `data/staging` with
`--keep-failed-db`. Interrupted publication is recovered automatically on the
next invocation. Concurrent rebuild, discover-only, and plan-only operations
are rejected through `data/.rebuild.lock`.
EOF
cat >> INSTALLATION_MANIFEST.txt <<'EOF'

Multi-Repository Source Configuration v0.1
  workspace/{models,config,manifest,languages,registry,planner,cli,pipeline,multi_aidl,multi_service}.py
  config/source_roots.toml
  config/parser_registry.toml
  tests/unit/test_workspace_v01.py
  queries/workspace_coverage_summary.sql
  workspace/build_publish.py
  tests/unit/test_build_publish.py
  tests/integration/test_atomic_rebuild.py
  scripts/rebuild_all.sh

Atomic Database Rebuild v0.1
  Complete batches stage under data/staging/<build-id>.
  Database replacement is the final publication commit point.
  --keep-failed-db retains failed batches for diagnosis.
  Publication recovery uses data/.publish-journal.json.
  All canonical modes share data/.rebuild.lock.
  No additional root installer script is required.
EOF

log "Multi-Repository Source Configuration v0.1 completed"
echo "Canonical rebuild: cd $PROJECT_ROOT && ./scripts/rebuild_all.sh"
echo "Enable additional repositories in: $PROJECT_ROOT/config/source_roots.toml"
