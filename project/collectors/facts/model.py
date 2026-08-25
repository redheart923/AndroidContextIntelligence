from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Mapping, TypeAlias


_SHA256 = re.compile(r"[0-9a-f]{64}")


class EvidenceKind(StrEnum):
    SOURCE_DECLARATION = "source_declaration"
    EXPLICIT_REGISTRATION = "explicit_registration"
    NAMING_CONVENTION = "naming_convention"
    BUILD_DECLARATION = "build_declaration"
    GENERATED_BUILD_ARTIFACT = "generated_build_artifact"
    RESOLVED_REFERENCE = "resolved_reference"
    AMBIGUOUS_CANDIDATE = "ambiguous_candidate"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    APPROVED_CORRECTION = "approved_correction"


def _canonical_key(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): normalize_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset, list, tuple)):
        normalized = [normalize_value(item) for item in value]
        return sorted(normalized, key=_canonical_key)
    if isinstance(value, PurePosixPath):
        return value.as_posix()
    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported fact property value: {type(value).__name__}")


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        raise ValueError("source_path must not be empty")
    return PurePosixPath(normalized).as_posix()


@dataclass(frozen=True)
class SourceRange:
    source_path: str
    line_start: int | None = None
    line_end: int | None = None
    column_start: int | None = None
    column_end: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", _normalize_path(self.source_path))
        for name in ("line_start", "line_end", "column_start", "column_end"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} must be positive")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must not precede line_start")


@dataclass(frozen=True)
class Evidence:
    repository: str
    extractor: str
    extractor_version: str
    evidence_kind: EvidenceKind
    source_revision: str | None
    content_fingerprint: str
    platform_identity: str
    semantic_profile_version: str

    def __post_init__(self) -> None:
        try:
            kind = EvidenceKind(self.evidence_kind)
        except ValueError as error:
            raise ValueError(
                f"unknown evidence kind: {self.evidence_kind}"
            ) from error
        object.__setattr__(self, "evidence_kind", kind)
        for name in (
            "repository",
            "extractor",
            "extractor_version",
            "platform_identity",
            "semantic_profile_version",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty")
        if not _SHA256.fullmatch(self.content_fingerprint):
            raise ValueError("content_fingerprint must be a lowercase SHA-256")


@dataclass(frozen=True)
class SymbolFact:
    language: str
    fact_kind: str
    logical_identity: str
    source_range: SourceRange
    evidence: Evidence
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        _normalize_fact(self)


@dataclass(frozen=True)
class RelationFact:
    language: str
    fact_kind: str
    logical_identity: str
    from_identity: str
    to_identity: str
    source_range: SourceRange
    evidence: Evidence
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        _normalize_fact(self)


@dataclass(frozen=True)
class BuildModuleFact:
    language: str
    fact_kind: str
    logical_identity: str
    module_name: str
    module_kind: str
    source_range: SourceRange
    evidence: Evidence
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        _normalize_fact(self)


@dataclass(frozen=True)
class BuildActionFact:
    language: str
    fact_kind: str
    logical_identity: str
    rule: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    source_range: SourceRange
    evidence: Evidence
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        _normalize_fact(self)
        object.__setattr__(self, "inputs", tuple(sorted(self.inputs)))
        object.__setattr__(self, "outputs", tuple(sorted(self.outputs)))


@dataclass(frozen=True)
class InteropBindingFact:
    language: str
    fact_kind: str
    logical_identity: str
    managed_identity: str
    native_identity: str
    source_range: SourceRange
    evidence: Evidence
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        _normalize_fact(self)


@dataclass(frozen=True)
class CandidateFact:
    language: str
    fact_kind: str
    logical_identity: str
    candidate_kind: str
    subject_identity: str
    proposed_identity: str | None
    source_range: SourceRange
    evidence: Evidence
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        _normalize_fact(self)


@dataclass(frozen=True)
class DiagnosticFact:
    language: str
    fact_kind: str
    logical_identity: str
    category: str
    reason_code: str
    message: str
    source_range: SourceRange
    evidence: Evidence
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        _normalize_fact(self)


def _normalize_fact(fact: object) -> None:
    for name in ("language", "fact_kind", "logical_identity"):
        if not getattr(fact, name):
            raise ValueError(f"{name} must not be empty")
    properties = normalize_value(getattr(fact, "properties"))
    if not isinstance(properties, dict):
        raise TypeError("properties must be a mapping")
    object.__setattr__(fact, "properties", properties)


Fact: TypeAlias = (
    SymbolFact
    | RelationFact
    | BuildModuleFact
    | BuildActionFact
    | InteropBindingFact
    | CandidateFact
    | DiagnosticFact
)
