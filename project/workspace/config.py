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
