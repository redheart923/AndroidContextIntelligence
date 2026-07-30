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
    revision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "enabled": self.enabled,
                "include": list(self.include), "exclude": list(self.exclude),
                "languages": list(self.languages), "source": self.source,
                "status": self.status, "revision": self.revision}


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
    capability_quality: tuple[tuple[str, str], ...] = ()
    capability_evidence: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def quality_for(self, capability: str) -> str | None:
        if capability not in self.capabilities:
            return None
        return dict(self.capability_quality).get(capability, "semantic")

    def evidence_for(self, capability: str) -> tuple[str, ...]:
        return dict(self.capability_evidence).get(capability, ())


@dataclass(frozen=True)
class PlanTask:
    repository: str
    repository_path: str
    language: str
    capability: str
    parser: str | None
    status: str
    files: int
    quality: str | None = None
    expected_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = self.__dict__.copy()
        value["expected_evidence"] = list(self.expected_evidence)
        return value


@dataclass(frozen=True)
class WorkspacePlan:
    aosp_root: str
    repositories: tuple[RepositorySpec, ...]
    inventories: tuple[LanguageInventory, ...]
    tasks: tuple[PlanTask, ...]
    default_exclude: tuple[str, ...] = ()
    strict: bool = False
    strict_capability: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"aosp_root": self.aosp_root,
                "default_exclude": list(self.default_exclude),
                "strict": self.strict,
                "strict_capability": self.strict_capability,
                "repositories": [x.to_dict() for x in self.repositories],
                "inventories": [x.to_dict() for x in self.inventories],
                "tasks": [x.to_dict() for x in self.tasks]}
