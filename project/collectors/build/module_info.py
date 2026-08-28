from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from collectors.build.compile_commands import BuildMetadataResult, _evidence
from collectors.facts.model import BuildModuleFact, RelationFact, SourceRange


def load_module_info(
    path: Path,
    repository: str,
    revision: str | None,
    platform_identity: str,
) -> BuildMetadataResult:
    content = path.read_bytes()
    payload = json.loads(content)
    if not isinstance(payload, Mapping):
        raise ValueError("module-info must contain an object")
    artifact_revision = payload.get("source_revision")
    if artifact_revision and revision and str(artifact_revision) != revision:
        raise ValueError(
            f"build metadata revision mismatch: expected {revision}, got {artifact_revision}"
        )
    modules = payload.get("modules", payload)
    if not isinstance(modules, Mapping):
        raise ValueError("module-info modules must be an object")
    evidence = _evidence(path, content, repository, revision, platform_identity)
    location = SourceRange(source_path=path.as_posix(), line_start=1, line_end=1)
    facts: list[BuildModuleFact] = []
    relations: list[RelationFact] = []
    for name, raw in sorted(modules.items(), key=lambda item: str(item[0])):
        if name == "source_revision" or not isinstance(raw, Mapping):
            continue
        identity = f"soong:module:{name}"
        facts.append(
            BuildModuleFact(
                language="build-metadata",
                fact_kind="SOONG_MODULE_METADATA",
                logical_identity=identity,
                module_name=str(name),
                module_kind="module_info",
                source_range=location,
                evidence=evidence,
                properties={
                    "module_paths": list(raw.get("path", [])),  # type: ignore[arg-type]
                    "module_classes": list(raw.get("class", [])),  # type: ignore[arg-type]
                    "installed_files": list(raw.get("installed", [])),  # type: ignore[arg-type]
                },
            )
        )
        for dependency in raw.get("dependencies", []):  # type: ignore[union-attr]
            target = f"soong:module:{dependency}"
            relations.append(
                RelationFact(
                    language="build-metadata",
                    fact_kind="MODULE_INFO_DEPENDS_ON",
                    logical_identity=f"MODULE_INFO_DEPENDS_ON:{identity}->{target}",
                    from_identity=identity,
                    to_identity=target,
                    source_range=location,
                    evidence=evidence,
                    properties={},
                )
            )
    return BuildMetadataResult(tuple(facts), tuple(relations), (), ())
