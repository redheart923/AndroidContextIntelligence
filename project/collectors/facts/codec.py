from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Iterable, Mapping

from collectors.facts.model import (
    BuildActionFact,
    BuildModuleFact,
    CandidateFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    InteropBindingFact,
    RelationFact,
    SourceRange,
    SymbolFact,
    normalize_value,
)


_FACT_TYPES: dict[str, type[Fact]] = {
    item.__name__: item
    for item in (
        SymbolFact,
        RelationFact,
        BuildModuleFact,
        BuildActionFact,
        InteropBindingFact,
        CandidateFact,
        DiagnosticFact,
    )
}


def _record_value(value: object) -> object:
    if isinstance(value, SourceRange):
        return {
            field.name: _record_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Evidence):
        return {
            field.name: _record_value(getattr(value, field.name))
            for field in fields(value)
        }
    return normalize_value(value)


def fact_to_dict(fact: Fact) -> dict[str, object]:
    if type(fact).__name__ not in _FACT_TYPES:
        raise TypeError(f"unsupported typed fact: {type(fact).__name__}")
    return {
        "record_type": type(fact).__name__,
        **{
            field.name: _record_value(getattr(fact, field.name))
            for field in fields(fact)
        },
    }


def _fact_from_dict(value: Mapping[str, object]) -> Fact:
    record_type = value.get("record_type")
    fact_type = _FACT_TYPES.get(str(record_type))
    if fact_type is None:
        raise ValueError(f"unknown typed fact record_type: {record_type}")
    source = value.get("source_range")
    raw_evidence = value.get("evidence")
    if not isinstance(source, Mapping) or not isinstance(raw_evidence, Mapping):
        raise ValueError("typed fact requires source_range and evidence objects")
    evidence = dict(raw_evidence)
    evidence["evidence_kind"] = EvidenceKind(str(evidence["evidence_kind"]))
    payload = {
        key: item
        for key, item in value.items()
        if key not in {"record_type", "source_range", "evidence"}
    }
    payload["source_range"] = SourceRange(**source)  # type: ignore[arg-type]
    payload["evidence"] = Evidence(**evidence)  # type: ignore[arg-type]
    if fact_type is BuildActionFact:
        payload["inputs"] = tuple(payload["inputs"])  # type: ignore[arg-type]
        payload["outputs"] = tuple(payload["outputs"])  # type: ignore[arg-type]
    return fact_type(**payload)  # type: ignore[arg-type,return-value]


def _canonical_line(fact: Fact) -> str:
    return json.dumps(
        fact_to_dict(fact),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def write_facts(path: Path, facts: Iterable[Fact]) -> str:
    lines = sorted(_canonical_line(fact) for fact in facts)
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def read_facts(path: Path) -> tuple[Fact, ...]:
    records: list[Fact] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"typed fact line {line_number} is not an object")
        records.append(_fact_from_dict(value))
    return tuple(records)
