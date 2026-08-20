from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


_ACTIONS = frozenset({"suppress", "replace", "annotate", "add"})
_FACT_URI = re.compile(r"^(edge|node|external):(.+)$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CORRECTION_KEYS = frozenset(
    {
        "correction_id",
        "action",
        "target_fact_uri",
        "expected_content_hash",
        "applicable_source_revision",
        "reason",
        "evidence_refs",
        "author",
        "approved_by",
        "approval_ref",
        "replacement",
    }
)
_EDGE_REPLACEMENT_KEYS = frozenset(
    {
        "fact_kind",
        "edge_type",
        "from_node_id",
        "to_node_id",
        "properties",
        "source_path",
        "line_start",
        "line_end",
    }
)
_ANNOTATION_KEYS = frozenset({"confidence_class", "explanation"})


class CorrectionError(ValueError):
    pass


@dataclass(frozen=True)
class Correction:
    correction_id: str
    action: str
    target_fact_uri: str
    expected_content_hash: str
    applicable_source_revision: str
    reason: str
    evidence_refs: tuple[str, ...]
    author: str
    approved_by: str
    approval_ref: str
    replacement: Mapping[str, Any] | None
    source_path: str
    content_hash: str


@dataclass(frozen=True)
class CorrectionApplication:
    correction_id: str
    status: str
    target_content_hash: str | None
    effective_fact_uri: str | None
    message: str


@dataclass(frozen=True)
class CorrectionReport:
    applications: tuple[CorrectionApplication, ...]

    @property
    def applied(self) -> int:
        return sum(item.status == "applied" for item in self.applications)

    @property
    def stale(self) -> int:
        return sum(item.status == "stale" for item in self.applications)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    encoded = value if isinstance(value, str) else _canonical_json(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise CorrectionError(f"{key} must be a non-empty string")
    return item.strip()


def _validate_replacement(action: str, replacement: object) -> Mapping[str, Any] | None:
    if action in {"replace", "add"}:
        if not isinstance(replacement, dict):
            raise CorrectionError(f"{action} replacement payload must be complete")
        missing = sorted(_EDGE_REPLACEMENT_KEYS - replacement.keys())
        unknown = sorted(replacement.keys() - _EDGE_REPLACEMENT_KEYS)
        if missing or unknown or replacement.get("fact_kind") != "edge":
            raise CorrectionError(
                f"{action} replacement payload must be complete; "
                f"missing={missing} unknown={unknown}"
            )
        for key in ("edge_type", "from_node_id", "to_node_id", "source_path"):
            if not isinstance(replacement.get(key), str) or not replacement[key].strip():
                raise CorrectionError(f"replacement {key} must be a non-empty string")
        if not isinstance(replacement.get("properties"), dict):
            raise CorrectionError("replacement properties must be a table")
        for key in ("line_start", "line_end"):
            if not isinstance(replacement.get(key), int) or replacement[key] < 1:
                raise CorrectionError(f"replacement {key} must be a positive integer")
        if replacement["line_end"] < replacement["line_start"]:
            raise CorrectionError("replacement line_end precedes line_start")
        return dict(replacement)
    if action == "annotate":
        if not isinstance(replacement, dict) or not replacement:
            raise CorrectionError("annotate replacement payload is required")
        unknown = sorted(replacement.keys() - _ANNOTATION_KEYS)
        if unknown:
            raise CorrectionError(
                "annotate may change confidence/explanation only; "
                f"unknown={unknown}"
            )
        for key, item in replacement.items():
            if not isinstance(item, str) or not item.strip():
                raise CorrectionError(f"annotation {key} must be a non-empty string")
        return dict(replacement)
    if replacement is not None:
        raise CorrectionError("suppress does not accept a replacement payload")
    return None


def parse_correction(
    value: Mapping[str, object], source_path: str = "<memory>"
) -> Correction:
    unknown = sorted(value.keys() - _CORRECTION_KEYS)
    if unknown:
        raise CorrectionError(f"unknown correction keys: {unknown}")
    correction_id = _required_text(value, "correction_id")
    action = _required_text(value, "action")
    if action not in _ACTIONS:
        raise CorrectionError(f"unsupported correction action: {action}")
    target_fact_uri = _required_text(value, "target_fact_uri")
    match = _FACT_URI.fullmatch(target_fact_uri)
    if match is None:
        raise CorrectionError(f"invalid target_fact_uri: {target_fact_uri}")
    replacement = _validate_replacement(action, value.get("replacement"))
    if action == "add" and match.group(1) != "external":
        raise CorrectionError("add corrections require an external: target_fact_uri")
    if action != "add" and match.group(1) == "external":
        raise CorrectionError(f"{action} corrections require an edge: or node: target")
    expected_hash = _required_text(value, "expected_content_hash")
    if _HASH.fullmatch(expected_hash) is None:
        raise CorrectionError("expected_content_hash must be a lowercase SHA-256 digest")
    evidence_refs = value.get("evidence_refs")
    if (
        not isinstance(evidence_refs, list)
        or not evidence_refs
        or any(not isinstance(item, str) or not item.strip() for item in evidence_refs)
    ):
        raise CorrectionError("evidence_refs must be a non-empty string array")
    normalized = {
        "correction_id": correction_id,
        "action": action,
        "target_fact_uri": target_fact_uri,
        "expected_content_hash": expected_hash,
        "applicable_source_revision": _required_text(value, "applicable_source_revision"),
        "reason": _required_text(value, "reason"),
        "evidence_refs": tuple(item.strip() for item in evidence_refs),
        "author": _required_text(value, "author"),
        "approved_by": _required_text(value, "approved_by"),
        "approval_ref": _required_text(value, "approval_ref"),
        "replacement": replacement,
        "source_path": source_path,
    }
    return Correction(**normalized, content_hash=_digest(normalized))


def load_corrections(directory: Path) -> tuple[Correction, ...]:
    if not directory.is_dir():
        raise CorrectionError(f"corrections directory does not exist: {directory}")
    result: list[Correction] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.toml")):
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise CorrectionError(f"cannot read correction file {path}: {error}") from error
        entries = document.get("corrections")
        if not isinstance(entries, list):
            raise CorrectionError(f"{path}: expected [[corrections]] entries")
        unknown_top = sorted(document.keys() - {"corrections"})
        if unknown_top:
            raise CorrectionError(f"{path}: unknown top-level keys: {unknown_top}")
        for raw in entries:
            if not isinstance(raw, dict):
                raise CorrectionError(f"{path}: correction entry must be a table")
            correction = parse_correction(raw, path.as_posix())
            if correction.correction_id in seen:
                raise CorrectionError(
                    f"duplicate correction_id: {correction.correction_id}"
                )
            seen.add(correction.correction_id)
            result.append(correction)
    return tuple(sorted(result, key=lambda item: item.correction_id))


def validate_corrections(corrections: Iterable[Correction]) -> tuple[Correction, ...]:
    ordered = tuple(sorted(corrections, key=lambda item: item.correction_id))
    ids: set[str] = set()
    effective_targets: set[str] = set()
    for correction in ordered:
        if correction.correction_id in ids:
            raise CorrectionError(f"duplicate correction_id: {correction.correction_id}")
        ids.add(correction.correction_id)
        if correction.action in {"suppress", "replace"}:
            if correction.target_fact_uri in effective_targets:
                raise CorrectionError(
                    f"conflicting effective corrections for {correction.target_fact_uri}"
                )
            effective_targets.add(correction.target_fact_uri)
    return ordered


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _target(connection: sqlite3.Connection, uri: str) -> tuple[str, str] | None:
    kind, identity = uri.split(":", 1)
    if kind == "edge":
        row = connection.execute(
            "SELECT content_hash, COALESCE(source_revision, '') FROM edge WHERE edge_id=?",
            (identity,),
        ).fetchone()
    elif kind == "node":
        row = connection.execute(
            "SELECT content_hash, COALESCE(source_revision, '') FROM node WHERE node_id=?",
            (identity,),
        ).fetchone()
    else:
        return None
    if row is None:
        return None
    return str(row[0] or ""), str(row[1] or "")


def _insert_node(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    node_type: str,
    display_name: str,
    properties: Mapping[str, Any],
    source_revision: str,
) -> None:
    properties_json = _canonical_json(properties)
    content_hash = _digest(
        [node_type, node_id, display_name, properties, source_revision]
    )
    connection.execute(
        """
        INSERT INTO node VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(node_id) DO UPDATE SET
          properties_json=excluded.properties_json,
          content_hash=excluded.content_hash,
          status='active',
          updated_at=excluded.updated_at
        """,
        (
            node_id,
            node_type,
            node_id,
            display_name,
            properties_json,
            None,
            None,
            None,
            source_revision,
            "correction",
            "1.0.0",
            content_hash,
            "active",
            _now(),
        ),
    )


def _insert_edge(
    connection: sqlite3.Connection,
    *,
    edge_type: str,
    from_node_id: str,
    to_node_id: str,
    properties: Mapping[str, Any],
    source_path: str | None,
    line_start: int | None,
    line_end: int | None,
    source_revision: str,
    identity: str,
    extractor: str = "correction",
) -> str:
    properties_json = _canonical_json(properties)
    edge_id = _digest(["correction-edge", identity])
    content_hash = _digest(
        [
            edge_type,
            from_node_id,
            to_node_id,
            properties,
            source_path,
            line_start,
            line_end,
            source_revision,
        ]
    )
    connection.execute(
        """
        INSERT INTO edge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(edge_id) DO UPDATE SET
          properties_json=excluded.properties_json,
          content_hash=excluded.content_hash,
          status='active',
          updated_at=excluded.updated_at
        """,
        (
            edge_id,
            edge_type,
            from_node_id,
            to_node_id,
            properties_json,
            source_path,
            line_start,
            line_end,
            source_revision,
            extractor,
            "1.0.0",
            content_hash,
            "active",
            _now(),
        ),
    )
    return edge_id


def _materialize_reviewed_edge(
    connection: sqlite3.Connection,
    correction: Correction,
    source_revision: str,
) -> str:
    assert correction.replacement is not None
    replacement = correction.replacement
    properties = dict(replacement["properties"])
    properties.update(
        {
            "correction_id": correction.correction_id,
            "origin": "reviewed_correction",
            "approval_ref": correction.approval_ref,
        }
    )
    edge_id = _insert_edge(
        connection,
        edge_type=str(replacement["edge_type"]),
        from_node_id=str(replacement["from_node_id"]),
        to_node_id=str(replacement["to_node_id"]),
        properties=properties,
        source_path=str(replacement["source_path"]),
        line_start=int(replacement["line_start"]),
        line_end=int(replacement["line_end"]),
        source_revision=source_revision,
        identity=f"{correction.correction_id}:reviewed",
    )
    if correction.action == "replace":
        correction_node = f"FACT_CORRECTION:{correction.correction_id}"
        reviewed_node = f"REVIEWED_FACT:{correction.correction_id}"
        raw_node = f"FACT_REFERENCE:{_digest(correction.target_fact_uri)}"
        _insert_node(
            connection,
            node_id=correction_node,
            node_type="FACT_CORRECTION",
            display_name=correction.correction_id,
            properties={"approval_ref": correction.approval_ref},
            source_revision=source_revision,
        )
        _insert_node(
            connection,
            node_id=reviewed_node,
            node_type="REVIEWED_FACT",
            display_name=edge_id,
            properties={"effective_fact_uri": f"edge:{edge_id}"},
            source_revision=source_revision,
        )
        _insert_node(
            connection,
            node_id=raw_node,
            node_type="FACT_REFERENCE",
            display_name=correction.target_fact_uri,
            properties={"fact_uri": correction.target_fact_uri},
            source_revision=source_revision,
        )
        for edge_type in ("SUPERSEDES", "CONTRADICTS"):
            _insert_edge(
                connection,
                edge_type=edge_type,
                from_node_id=reviewed_node,
                to_node_id=raw_node,
                properties={
                    "correction_id": correction.correction_id,
                    "correction_node_id": correction_node,
                },
                source_path=correction.source_path,
                line_start=None,
                line_end=None,
                source_revision=source_revision,
                identity=f"{correction.correction_id}:{edge_type}",
                extractor="correction_provenance",
            )
    return f"edge:{edge_id}"


def _store_correction(
    connection: sqlite3.Connection,
    correction: Correction,
    lifecycle_status: str,
) -> None:
    connection.execute(
        """
        INSERT INTO fact_correction VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(correction_id) DO UPDATE SET
          lifecycle_status=excluded.lifecycle_status,
          content_hash=excluded.content_hash
        """,
        (
            correction.correction_id,
            correction.action,
            correction.target_fact_uri,
            correction.expected_content_hash,
            correction.applicable_source_revision,
            correction.reason,
            _canonical_json(correction.evidence_refs),
            correction.author,
            correction.approved_by,
            correction.approval_ref,
            None
            if correction.replacement is None
            else _canonical_json(correction.replacement),
            lifecycle_status,
            correction.source_path,
            correction.content_hash,
        ),
    )


def _store_application(
    connection: sqlite3.Connection,
    correction: Correction,
    run_id: str,
    application: CorrectionApplication,
) -> None:
    application_hash = _digest(
        {
            "correction_id": correction.correction_id,
            "run_id": run_id,
            "status": application.status,
            "target_content_hash": application.target_content_hash,
            "effective_fact_uri": application.effective_fact_uri,
            "message": application.message,
        }
    )
    connection.execute(
        """
        INSERT INTO correction_application VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(correction_id, run_id) DO UPDATE SET
          application_status=excluded.application_status,
          target_content_hash=excluded.target_content_hash,
          effective_fact_uri=excluded.effective_fact_uri,
          message=excluded.message,
          content_hash=excluded.content_hash
        """,
        (
            correction.correction_id,
            run_id,
            application.status,
            application.target_content_hash,
            application.effective_fact_uri,
            application.message,
            application_hash,
        ),
    )


def apply_corrections(
    database: Path,
    corrections: Iterable[Correction],
    *,
    source_revision: str,
    run_id: str,
) -> CorrectionReport:
    ordered = validate_corrections(corrections)
    applications: list[CorrectionApplication] = []
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute(
            "SELECT 1 FROM extraction_run WHERE run_id=?", (run_id,)
        ).fetchone() is None:
            raise CorrectionError(f"unknown extraction run: {run_id}")
        connection.execute("BEGIN IMMEDIATE")
        for correction in ordered:
            target = _target(connection, correction.target_fact_uri)
            target_hash = target[0] if target is not None else None
            revision_matches = (
                correction.applicable_source_revision == source_revision
            )
            hash_matches = (
                correction.action == "add"
                or target_hash == correction.expected_content_hash
            )
            target_exists = correction.action == "add" or target is not None
            if not (revision_matches and hash_matches and target_exists):
                reasons = []
                if not target_exists:
                    reasons.append("target missing")
                if not revision_matches:
                    reasons.append("source revision mismatch")
                if target_exists and not hash_matches:
                    reasons.append("content hash mismatch")
                application = CorrectionApplication(
                    correction_id=correction.correction_id,
                    status="stale",
                    target_content_hash=target_hash,
                    effective_fact_uri=correction.target_fact_uri if target_exists else None,
                    message="; ".join(reasons),
                )
                _store_correction(connection, correction, "stale")
                _store_application(connection, correction, run_id, application)
                applications.append(application)
                continue
            effective_uri = correction.target_fact_uri
            if correction.action in {"replace", "add"}:
                effective_uri = _materialize_reviewed_edge(
                    connection, correction, source_revision
                )
            application = CorrectionApplication(
                correction_id=correction.correction_id,
                status="applied",
                target_content_hash=target_hash,
                effective_fact_uri=(
                    None if correction.action == "suppress" else effective_uri
                ),
                message=f"approved {correction.action} correction applied",
            )
            _store_correction(connection, correction, "active")
            _store_application(connection, correction, run_id, application)
            applications.append(application)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise CorrectionError(f"foreign-key violations: {violations[:5]}")
        connection.execute("COMMIT")
    except (sqlite3.Error, CorrectionError) as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        if isinstance(error, CorrectionError):
            raise
        raise CorrectionError(f"failed to apply corrections: {error}") from error
    finally:
        connection.close()
    return CorrectionReport(tuple(applications))
