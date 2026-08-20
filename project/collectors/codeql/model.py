from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any


class RecordError(ValueError):
    pass


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SourceSpan:
    repository_path: str
    source_path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def __post_init__(self) -> None:
        coordinates = (
            self.start_line,
            self.start_column,
            self.end_line,
            self.end_column,
        )
        if any(value < 1 for value in coordinates):
            raise RecordError(f"invalid source span: {coordinates!r}")
        if (self.end_line, self.end_column) < (
            self.start_line,
            self.start_column,
        ):
            raise RecordError(f"reversed source span: {coordinates!r}")


@dataclass(frozen=True)
class CallTargetRecord:
    callee_symbol_key: str
    relation_kind: str

    def __post_init__(self) -> None:
        if self.relation_kind not in {"must", "may"}:
            raise RecordError(f"invalid relation_kind: {self.relation_kind!r}")
        if not self.callee_symbol_key:
            raise RecordError("call target must have a callee_symbol_key")


@dataclass(frozen=True)
class DefinitionRecord:
    language: str
    package_name: str
    declaring_type: str
    callable_kind: str
    callable_name: str
    erased_parameters: tuple[str, ...]
    return_type: str
    symbol_key: str
    span: SourceSpan
    query_id: str
    query_version: str
    database_fingerprint: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_hash", canonical_hash(record_payload(self)))


@dataclass(frozen=True)
class CallSiteRecord:
    language: str
    caller_symbol_key: str
    dispatch_kind: str
    relation_kind: str
    candidate_count: int
    expression_text: str
    unresolved_reason: str
    span: SourceSpan
    targets: tuple[CallTargetRecord, ...]
    query_id: str
    query_version: str
    database_fingerprint: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.relation_kind not in {"must", "may", "unresolved"}:
            raise RecordError(f"invalid relation_kind: {self.relation_kind!r}")
        if self.candidate_count < 0:
            raise RecordError("candidate_count must not be negative")
        if self.relation_kind == "unresolved":
            if self.candidate_count != 0 or self.targets or not self.unresolved_reason:
                raise RecordError("unresolved call-site evidence is inconsistent")
        elif self.candidate_count != len(self.targets):
            raise RecordError("candidate_count does not match explicit targets")
        object.__setattr__(self, "content_hash", canonical_hash(record_payload(self)))


@dataclass(frozen=True)
class ProgramValueRecord:
    identity: str
    symbol_key: str
    value_kind: str
    ordinal: int
    span: SourceSpan

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise RecordError("program-value ordinal must not be negative")


@dataclass(frozen=True)
class DataflowPathRecord:
    scenario: str
    entry_symbol_key: str
    sink_symbol_key: str
    steps: tuple[ProgramValueRecord, ...]
    query_id: str
    query_version: str
    database_fingerprint: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        ordinals = tuple(step.ordinal for step in self.steps)
        if ordinals != tuple(range(len(self.steps))):
            raise RecordError(f"duplicate or unordered path ordinal: {ordinals!r}")
        object.__setattr__(self, "content_hash", canonical_hash(record_payload(self)))


@dataclass(frozen=True)
class GuardRecord:
    owner_symbol_key: str
    guard_callable: str
    sink_callable: str
    relation_kind: str
    guard_line: int
    sink_line: int
    source_path: str
    query_id: str
    query_version: str
    database_fingerprint: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.relation_kind != "dominates":
            raise RecordError(f"invalid guard relation_kind: {self.relation_kind!r}")
        if min(self.guard_line, self.sink_line) < 1:
            raise RecordError("guard source line must be positive")
        object.__setattr__(self, "content_hash", canonical_hash(record_payload(self)))


@dataclass(frozen=True)
class IdentityTransitionRecord:
    owner_symbol_key: str
    clear_line: int
    restore_line: int | None
    status: str
    source_path: str
    query_id: str
    query_version: str
    database_fingerprint: str
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.status not in {"paired_all_exits", "missing_all_exit_restore"}:
            raise RecordError(f"invalid identity status: {self.status!r}")
        if self.clear_line < 1 or (self.restore_line is not None and self.restore_line < 1):
            raise RecordError("identity source line must be positive")
        if self.status == "paired_all_exits" and self.restore_line is None:
            raise RecordError("paired identity transition has no restore line")
        if self.status != "paired_all_exits" and self.restore_line is not None:
            raise RecordError("unsafe identity transition must not claim a restore line")
        object.__setattr__(self, "content_hash", canonical_hash(record_payload(self)))


NormalizedRecord = (
    DefinitionRecord
    | CallSiteRecord
    | DataflowPathRecord
    | GuardRecord
    | IdentityTransitionRecord
)


def _stable_value(value: object) -> object:
    if is_dataclass(value):
        return {
            item.name: _stable_value(getattr(value, item.name))
            for item in fields(value)
            if item.name != "content_hash"
        }
    if isinstance(value, (tuple, list)):
        return [_stable_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _stable_value(item) for key, item in value.items()}
    return value


def record_payload(record: object) -> dict[str, Any]:
    value = _stable_value(record)
    if not isinstance(value, dict):
        raise RecordError("record payload must be an object")
    return value


def record_to_dict(record: NormalizedRecord) -> dict[str, Any]:
    payload = asdict(record)
    payload["record_type"] = type(record).__name__
    return payload
