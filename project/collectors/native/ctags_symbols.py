from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from collectors.facts.model import Evidence, EvidenceKind, SourceRange, SymbolFact
from collectors.native.identity import logical_symbol_identity, qualified_native_name


EXTRACTOR = "universal-ctags-native"
EXTRACTOR_VERSION = "native-symbols-v0.1"
SEMANTIC_PROFILE = "native-static-v0.1"


_C_KINDS = {
    "function": ("C_FUNCTION", "function", True),
    "prototype": ("C_FUNCTION", "function", True),
    "struct": ("C_TYPE", "type", False),
    "union": ("C_TYPE", "type", False),
    "enum": ("C_TYPE", "type", False),
    "typedef": ("C_TYPE", "type", False),
    "macro": ("C_MACRO", "macro", False),
}

_CPP_KINDS = {
    "namespace": ("CPP_NAMESPACE", "namespace", False),
    "class": ("CPP_TYPE", "type", False),
    "struct": ("CPP_TYPE", "type", False),
    "union": ("CPP_TYPE", "type", False),
    "enum": ("CPP_TYPE", "type", False),
    "typedef": ("CPP_TYPE", "type", False),
    "function": ("CPP_FUNCTION", "function", True),
    "prototype": ("CPP_FUNCTION", "function", True),
    "method": ("CPP_METHOD", "method", True),
    "member": ("CPP_FIELD", "field", False),
    "field": ("CPP_FIELD", "field", False),
}


def ctags_command(root: Path, language: str, output: Path) -> list[str]:
    ctags_language = {"c": "C", "cpp": "C++"}.get(language)
    if ctags_language is None:
        raise ValueError(f"unsupported native ctags language: {language}")
    return [
        "ctags",
        "--output-format=json",
        "--fields=+nKSEs",
        "--extras=-F",
        f"--languages={ctags_language}",
        "--recurse=yes",
        "--append=no",
        "-f",
        str(output),
        str(root),
    ]


def _canonical_record(record: dict[str, object]) -> bytes:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _source_path(value: object, repository: str) -> str:
    raw = str(value or "").replace("\\", "/")
    if not raw:
        raise ValueError("native ctags record has no source path")
    normalized_repository = repository.strip("/")
    marker = f"/{normalized_repository}/"
    searchable = f"/{raw.lstrip('/')}"
    if marker in searchable:
        raw = normalized_repository + "/" + searchable.split(marker, 1)[1]
    elif len(raw) >= 3 and raw[1:3] == ":/":
        raw = raw[3:]
    return PurePosixPath(raw.lstrip("/")).as_posix()


def _language(record: dict[str, object]) -> tuple[str, dict[str, tuple[str, str, bool]]] | None:
    raw = str(record.get("language", "")).lower()
    if raw == "c":
        return "c", _C_KINDS
    if raw in {"c++", "cpp"}:
        return "cpp", _CPP_KINDS
    return None


def _return_type(record: dict[str, object]) -> str | None:
    value = record.get("typeref")
    if not value:
        return None
    raw = str(value)
    return raw.split(":", 1)[1] if ":" in raw else raw


def decode_ctags_record(
    record: dict[str, object],
    repository: str,
) -> tuple[SymbolFact, ...]:
    language_definition = _language(record)
    if language_definition is None or record.get("_type") not in {None, "tag"}:
        return ()
    language, kind_map = language_definition
    kind = str(record.get("kind", ""))
    mapped = kind_map.get(kind)
    name = str(record.get("name", ""))
    if mapped is None or not name:
        return ()
    fact_kind, category, include_signature = mapped
    qualified = qualified_native_name(
        name,
        str(record["scope"]) if record.get("scope") else None,
        str(record["signature"]) if record.get("signature") else None,
        include_signature=include_signature,
    )
    line_start = record.get("line") if isinstance(record.get("line"), int) else None
    raw_end = record.get("end")
    line_end = raw_end if isinstance(raw_end, int) else line_start
    content_fingerprint = hashlib.sha256(_canonical_record(record)).hexdigest()
    source_range = SourceRange(
        source_path=_source_path(record.get("path"), repository),
        line_start=line_start,
        line_end=line_end,
    )
    evidence = Evidence(
        repository=repository,
        extractor=EXTRACTOR,
        extractor_version=str(record.get("_ctags_version", EXTRACTOR_VERSION)),
        evidence_kind=EvidenceKind.SOURCE_DECLARATION,
        source_revision=(
            str(record["_source_revision"])
            if record.get("_source_revision")
            else None
        ),
        content_fingerprint=content_fingerprint,
        platform_identity=str(record.get("_platform_identity", "unknown")),
        semantic_profile_version=SEMANTIC_PROFILE,
    )
    return (
        SymbolFact(
            language=language,
            fact_kind=fact_kind,
            logical_identity=logical_symbol_identity(language, category, qualified),
            source_range=source_range,
            evidence=evidence,
            properties={
                "access": record.get("access"),
                "ctags_kind": kind,
                "scope": record.get("scope"),
                "scope_kind": record.get("scopeKind"),
                "signature": record.get("signature"),
                "return_type": _return_type(record),
            },
        ),
    )
