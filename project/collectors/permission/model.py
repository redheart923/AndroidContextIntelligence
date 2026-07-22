from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum, StrEnum
from typing import Any, Mapping


PARSER_VERSION = "permission-semantics-v0.1"


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def json_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


class PermissionFactKind(StrEnum):
    DECLARES_PERMISSION = "DECLARES_PERMISSION"
    REQUESTS_PERMISSION = "REQUESTS_PERMISSION"
    ALLOWLISTS_PRIVILEGED_PERMISSION = "ALLOWLISTS_PRIVILEGED_PERMISSION"
    DENIES_PRIVILEGED_PERMISSION = "DENIES_PRIVILEGED_PERMISSION"
    DEFAULT_GRANTS_PERMISSION = "DEFAULT_GRANTS_PERMISSION"
    REQUIRES_PERMISSION = "REQUIRES_PERMISSION"
    CHECKS_PERMISSION = "CHECKS_PERMISSION"
    ENFORCES_PERMISSION = "ENFORCES_PERMISSION"


@dataclass(frozen=True)
class PermissionEvidence:
    repository: str
    source_path: str
    source_dialect: str
    source_expression: str | None
    line_start: int | None
    line_end: int | None
    source_revision: str | None
    parser: str
    parser_version: str = PARSER_VERSION

    def to_dict(self) -> dict[str, object]:
        return json_value(asdict(self))


@dataclass(frozen=True)
class PermissionFact:
    kind: PermissionFactKind
    permission_name: str
    package_name: str | None
    owner_node_id: str | None
    properties: Mapping[str, object]
    evidence: PermissionEvidence

    @property
    def identity(self) -> str:
        return stable_identity(self.to_dict(include_identity=False))

    def to_dict(self, *, include_identity: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind.value,
            "permission_name": self.permission_name,
            "package_name": self.package_name,
            "owner_node_id": self.owner_node_id,
            "properties": json_value(self.properties),
            "evidence": self.evidence.to_dict(),
        }
        if include_identity:
            payload["identity"] = self.identity
        return payload


@dataclass(frozen=True)
class PermissionDiagnostic:
    category: str
    reason_code: str
    repository: str
    source_path: str
    line_start: int | None
    line_end: int | None
    expression: str | None
    message: str
    properties: Mapping[str, object] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        return stable_identity(self.to_dict(include_identity=False))

    def to_dict(self, *, include_identity: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "category": self.category,
            "reason_code": self.reason_code,
            "repository": self.repository,
            "source_path": self.source_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "expression": self.expression,
            "message": self.message,
            "properties": json_value(self.properties),
        }
        if include_identity:
            payload["identity"] = self.identity
        return payload


@dataclass(frozen=True)
class ParseOutcome:
    facts: tuple[PermissionFact, ...] = ()
    diagnostics: tuple[PermissionDiagnostic, ...] = ()
    counters: Mapping[str, int] = field(default_factory=dict)
