from __future__ import annotations

from pathlib import Path
from typing import Mapping

from collectors.build.compile_commands import (
    BuildMetadataResult,
    _evidence,
    _load_payload,
)
from collectors.facts.model import BuildModuleFact, EvidenceKind, RelationFact, SourceRange


def load_rust_project(
    path: Path,
    repository: str,
    revision: str | None,
    platform_identity: str,
) -> BuildMetadataResult:
    crates, content = _load_payload(path, revision, "crates")
    base_evidence = _evidence(path, content, repository, revision, platform_identity)
    evidence = type(base_evidence)(
        **{**base_evidence.__dict__, "evidence_kind": EvidenceKind.GENERATED_BUILD_ARTIFACT}
    )
    location = SourceRange(source_path=path.as_posix(), line_start=1, line_end=1)
    facts: list[BuildModuleFact] = []
    names: dict[int, str] = {}
    raw_crates: list[Mapping[str, object]] = []
    for raw in crates:
        if not isinstance(raw, Mapping):
            raise ValueError("rust-project crate must be an object")
        crate_id = int(raw.get("crate_id", len(raw_crates)))
        name = str(raw.get("display_name") or f"crate-{crate_id}")
        names[crate_id] = name
        raw_crates.append(raw)
        facts.append(
            BuildModuleFact(
                language="rust",
                fact_kind="RUST_CRATE_METADATA",
                logical_identity=f"rust:crate:{name}",
                module_name=name,
                module_kind="rust_crate",
                source_range=location,
                evidence=evidence,
                properties={
                    "crate_id": crate_id,
                    "root_module": str(raw.get("root_module", "")),
                    "edition": str(raw.get("edition", "")),
                    "cfg": list(raw.get("cfg", [])),  # type: ignore[arg-type]
                    "workspace_member": bool(raw.get("is_workspace_member", False)),
                },
            )
        )
    relations: list[RelationFact] = []
    for raw in raw_crates:
        source_id = int(raw.get("crate_id", 0))
        for dependency in raw.get("deps", []):  # type: ignore[union-attr]
            if not isinstance(dependency, Mapping):
                continue
            target_id = int(dependency.get("crate", -1))
            if source_id not in names or target_id not in names:
                continue
            source = f"rust:crate:{names[source_id]}"
            target = f"rust:crate:{names[target_id]}"
            relations.append(
                RelationFact(
                    language="rust",
                    fact_kind="RUST_CRATE_DEPENDS_ON",
                    logical_identity=f"RUST_CRATE_DEPENDS_ON:{source}->{target}",
                    from_identity=source,
                    to_identity=target,
                    source_range=location,
                    evidence=evidence,
                    properties={"dependency_name": str(dependency.get("name", ""))},
                )
            )
    return BuildMetadataResult(tuple(facts), tuple(relations), (), ())
