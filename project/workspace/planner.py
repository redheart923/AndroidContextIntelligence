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
