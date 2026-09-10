from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from typing import Iterable
from .config import load_workspace_config
from .languages import detect_languages
from .manifest import parse_repo_manifest
from .models import LanguageInventory, PlanTask, RepositorySpec, WorkspacePlan
from .registry import load_parser_registry
from .revisions import inspect_repository_provenance

CAPABILITIES = {"java": ("symbols", "inheritance", "service_registration", "permission_semantics", "call_graph", "interprocedural_dataflow", "jni_bindings"),
                "aidl": ("symbols", "binder"), "kotlin": ("symbols", "inheritance", "service_registration", "permission_semantics", "call_graph", "interprocedural_dataflow", "jni_bindings"),
                "xml": ("permission_semantics",),
                "c": ("native_symbols", "native_types", "native_includes"),
                "cpp": ("native_symbols", "native_types", "native_includes", "jni_bindings"),
                "rust": ("native_symbols", "native_types", "rust_ffi"),
                "hidl": ("symbols", "binder"),
                "python": ("symbols",), "blueprint": ("soong_build_graph",),
                "make": ("build",), "proto": ("symbols",)}


class CoverageError(RuntimeError):
    pass


def build_workspace_plan(
    config_path: Path,
    registry_path: Path,
    strict: bool = False,
    strict_capability: str | None = None,
    local_config_path: Path | None = None,
    *,
    strict_capabilities: Iterable[str] = (),
) -> WorkspacePlan:
    config = load_workspace_config(config_path, local_config_path)
    registry = load_parser_registry(registry_path)
    selected_capabilities = tuple(
        sorted(
            {
                item
                for item in (*strict_capabilities, strict_capability)
                if item
            }
        )
    )
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
        provenance = (
            inspect_repository_provenance(
                location,
                repo.include,
                tuple(config.default_exclude) + tuple(repo.exclude),
                repo.languages,
            )
            if repo.enabled
            else None
        )
        repo = replace(
            repo,
            status=status,
            revision=provenance.revision if provenance else None,
            revision_state=provenance.state if provenance else "not_inspected",
            revision_dirty=provenance.dirty if provenance else None,
            inventory_sha256=(
                provenance.inventory_sha256 if provenance else None
            ),
            inventory_file_count=provenance.file_count if provenance else 0,
        )
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
                    parser.implementation_for(capability) if parser else None,
                    status, count,
                    parser.quality_for(capability) if parser else None,
                    parser.evidence_for(capability) if parser else ())
                tasks.append(task)
                if status != "scheduled" and (
                    not selected_capabilities or capability in selected_capabilities
                ):
                    gaps.append(task)
    effective_strict = strict or config.strict or bool(selected_capabilities)
    if effective_strict and gaps:
        sample = ", ".join(f"{x.repository}:{x.language}:{x.capability}:{x.status}" for x in gaps[:8])
        raise CoverageError(f"workspace coverage gaps: {sample}")
    return WorkspacePlan(
        aosp_root=str(config.aosp_root),
        analysis_scope=config.analysis_scope,
        full_aosp_coverage=False,
        repositories=tuple(normalized),
        inventories=tuple(inventories),
        tasks=tuple(tasks),
        default_exclude=config.default_exclude,
        strict=effective_strict,
        strict_capabilities=selected_capabilities,
    )
