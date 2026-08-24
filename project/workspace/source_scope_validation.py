from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from workspace.config import KNOWN


class SourceScopeError(RuntimeError):
    pass


def _fail(reason_code: str, detail: str) -> None:
    raise SourceScopeError(f"{reason_code}: {detail}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def scope_report_fingerprint(payload: object) -> str:
    value = payload
    if isinstance(payload, dict):
        value = {
            key: item
            for key, item in payload.items()
            if key not in {"fingerprint", "build_id"}
        }
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def write_scope_report(
    path: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    value = dict(payload)
    value["fingerprint"] = scope_report_fingerprint(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return value


def bind_publication_scope_fingerprint(
    fingerprints_path: Path,
    scope_report_path: Path,
) -> dict[str, object]:
    fingerprints = _load_json(fingerprints_path)
    scope = _load_json(scope_report_path)
    if not isinstance(fingerprints, dict) or not isinstance(scope, dict):
        raise SourceScopeError("invalid fingerprint or source scope payload")
    if scope.get("fingerprint") != scope_report_fingerprint(scope):
        raise SourceScopeError("source scope fingerprint mismatch")
    payload = dict(fingerprints)
    payload["publication_scope"] = scope_report_fingerprint(scope)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{fingerprints_path.name}.",
        suffix=".tmp",
        dir=fingerprints_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, fingerprints_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return payload


def _enabled_repositories(plan: dict[str, object]) -> list[dict[str, object]]:
    repositories = plan.get("repositories", [])
    if not isinstance(repositories, list):
        _fail("no_enabled_repository", "repositories must be an array")
    enabled = [dict(item) for item in repositories if item.get("enabled")]
    if not enabled:
        _fail("no_enabled_repository", "no repository is enabled")
    return sorted(enabled, key=lambda item: (str(item.get("name")), str(item.get("path"))))


def _repository_identity(item: dict[str, object]) -> dict[str, object]:
    return {
        "name": item.get("name"),
        "path": item.get("path"),
        "revision": item.get("revision"),
        "revision_state": item.get("revision_state", item.get("state")),
        "inventory_sha256": item.get("inventory_sha256"),
        "file_count": int(
            item.get("inventory_file_count", item.get("file_count", 0)) or 0
        ),
    }


def _scope_header(plan: dict[str, object]) -> dict[str, object]:
    analysis_scope = plan.get("analysis_scope", "aosp")
    if analysis_scope not in {"aosp", "partial"}:
        _fail("scope_repository_mismatch", f"invalid analysis_scope {analysis_scope!r}")
    return {
        "schema_version": 1,
        "analysis_scope": analysis_scope,
        "full_aosp_coverage": False,
    }


def validate_preflight(plan: dict[str, object]) -> dict[str, object]:
    header = _scope_header(plan)
    enabled = _enabled_repositories(plan)
    names = [str(item.get("name", "")) for item in enabled]
    paths = [str(item.get("path", "")) for item in enabled]
    if any(not value for value in (*names, *paths)):
        _fail("scope_repository_mismatch", "enabled repository has empty name or path")
    if len(names) != len(set(names)) or len(paths) != len(set(paths)):
        _fail("scope_repository_mismatch", "duplicate repository name or path")

    identities: list[dict[str, object]] = []
    for repository in enabled:
        name = str(repository["name"])
        if repository.get("status") != "available":
            _fail(
                "missing_enabled_repository",
                f"{name}:{repository.get('path')}",
            )
        identity = _repository_identity(repository)
        if int(identity["file_count"]) <= 0:
            _fail("empty_repository_inventory", name)
        if not identity["inventory_sha256"]:
            _fail("scope_repository_mismatch", f"{name}:missing inventory_sha256")
        if identity["revision_state"] in {None, "", "not_inspected", "unknown"}:
            _fail("scope_repository_mismatch", f"{name}:missing revision_state")
        identities.append(identity)

    inventories = plan.get("inventories", [])
    recognized_files = 0
    if isinstance(inventories, list):
        enabled_names = set(names)
        for inventory in inventories:
            if inventory.get("repository") not in enabled_names:
                continue
            counts = inventory.get("counts", {})
            if not isinstance(counts, dict):
                continue
            recognized_files += sum(
                int(count or 0)
                for language, count in counts.items()
                if language in KNOWN
            )
    if recognized_files <= 0:
        _fail("no_recognized_language", "enabled inventories have no known language")

    tasks = plan.get("tasks", [])
    scheduled = (
        [item for item in tasks if item.get("status") == "scheduled"]
        if isinstance(tasks, list)
        else []
    )
    if not scheduled:
        _fail("no_scheduled_parser", "no scheduled parser task")

    return {
        **header,
        "build_id": None,
        "status": "preflight_passed",
        "enabled_repositories": identities,
        "scheduled_task_count": len(scheduled),
        "source_backed_node_count": 0,
        "capability_counts": {},
        "validation_errors": [],
    }


def _normalized(value: object) -> str:
    return str(value or "").replace("\\", "/").rstrip("/")


def _source_belongs_to_repository(
    source_path: str,
    repository: dict[str, object],
    aosp_root: str,
) -> bool:
    source = _normalized(source_path)
    path = _normalized(repository.get("path"))
    candidates = {path}
    if path and not Path(path).is_absolute():
        candidates.add(_normalized(Path(aosp_root) / path))
    return any(source == item or source.startswith(item + "/") for item in candidates if item)


def _count_owned_source_nodes(
    database: Path,
    repositories: list[dict[str, object]],
    aosp_root: str,
) -> int:
    try:
        connection = sqlite3.connect(database)
        rows = connection.execute(
            """
            SELECT properties_json, source_path
            FROM node
            WHERE node_type != 'GRAPH_BUILD'
              AND COALESCE(source_path, '') != ''
            """
        ).fetchall()
    except sqlite3.Error as error:
        _fail("no_source_backed_node", f"cannot inspect graph: {error}")
    finally:
        if "connection" in locals():
            connection.close()

    count = 0
    for raw_properties, source_path in rows:
        try:
            properties = json.loads(raw_properties or "{}")
        except json.JSONDecodeError:
            properties = {}
        declared_repository = properties.get("repository")
        for repository in repositories:
            if declared_repository not in {None, "", repository.get("name")}:
                continue
            if _source_belongs_to_repository(source_path, repository, aosp_root):
                count += 1
                break
    return count


def _task_key(item: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(item.get("repository")),
        str(item.get("language")),
        str(item.get("capability")),
    )


def _provenance_identities(payload: dict[str, object]) -> list[dict[str, object]]:
    repositories = payload.get("repositories", [])
    if not isinstance(repositories, list):
        _fail("scope_provenance_mismatch", "provenance repositories are absent")
    return sorted(
        (_repository_identity(dict(item)) for item in repositories),
        key=lambda item: (str(item.get("name")), str(item.get("path"))),
    )


def validate_post_import(
    plan: dict[str, object],
    database: Path,
    capability_report: list[dict[str, object]],
    provenance: dict[str, object],
    build_id: str,
) -> dict[str, object]:
    preflight = validate_preflight(plan)
    repositories = _enabled_repositories(plan)
    source_count = _count_owned_source_nodes(
        database,
        repositories,
        str(plan.get("aosp_root", "")),
    )
    if source_count <= 0:
        _fail("no_source_backed_node", "no node belongs to an enabled repository")

    scheduled = {
        _task_key(dict(item)): dict(item)
        for item in plan.get("tasks", [])
        if item.get("status") == "scheduled"
    }
    observed = {_task_key(dict(item)): dict(item) for item in capability_report}
    for key in sorted(scheduled):
        result = observed.get(key)
        status = result.get("status") if result else None
        if status not in {"supported", "degraded"}:
            _fail("capability_not_executed", ":".join(key))
        strict_capability = plan.get("strict_capability")
        if strict_capability == key[2] and status != "supported":
            _fail("strict_capability_gap", ":".join(key))

    planned_identities = preflight["enabled_repositories"]
    actual_identities = _provenance_identities(provenance)
    if planned_identities != actual_identities:
        _fail("scope_provenance_mismatch", "repository identity set differs")
    if any(item.get("matches_plan") is False for item in provenance.get("repositories", [])):
        _fail("scope_provenance_mismatch", "source changed during build")

    if plan.get("analysis_scope", "aosp") == "aosp":
        try:
            connection = sqlite3.connect(database)
            local_services = int(
                connection.execute(
                    "SELECT COUNT(*) FROM edge "
                    "WHERE edge_type='EXPOSED_AS_LOCAL_SERVICE'"
                ).fetchone()[0]
            )
        except sqlite3.Error as error:
            _fail("aosp_representative_evidence_missing", str(error))
        finally:
            if "connection" in locals():
                connection.close()
        if local_services < 1:
            _fail(
                "aosp_representative_evidence_missing",
                "no EXPOSED_AS_LOCAL_SERVICE edge",
            )

    counts = Counter(str(item.get("status")) for item in capability_report)
    return {
        **_scope_header(plan),
        "build_id": build_id,
        "status": "passed",
        "enabled_repositories": planned_identities,
        "scheduled_task_count": len(scheduled),
        "source_backed_node_count": source_count,
        "capability_counts": dict(sorted(counts.items())),
        "validation_errors": [],
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate source workspace scope")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--plan", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)
    post_import = commands.add_parser("post-import")
    post_import.add_argument("--plan", type=Path, required=True)
    post_import.add_argument("--db", type=Path, required=True)
    post_import.add_argument("--capability-report", type=Path, required=True)
    post_import.add_argument("--provenance", type=Path, required=True)
    post_import.add_argument("--build-id", required=True)
    post_import.add_argument("--output", type=Path, required=True)
    bind = commands.add_parser("bind-fingerprint")
    bind.add_argument("--fingerprints", type=Path, required=True)
    bind.add_argument("--scope-report", type=Path, required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    try:
        if parsed.command == "bind-fingerprint":
            bind_publication_scope_fingerprint(
                parsed.fingerprints,
                parsed.scope_report,
            )
            print("source_scope_validation: fingerprint_bound")
            return 0
        plan = _load_json(parsed.plan)
        if parsed.command == "preflight":
            payload = validate_preflight(plan)
        else:
            payload = validate_post_import(
                plan,
                parsed.db,
                _load_json(parsed.capability_report),
                _load_json(parsed.provenance),
                parsed.build_id,
            )
        write_scope_report(parsed.output, payload)
    except (OSError, json.JSONDecodeError, SourceScopeError) as error:
        print(f"ERROR: {error}")
        return 1
    print(f"source_scope_validation: {payload['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
