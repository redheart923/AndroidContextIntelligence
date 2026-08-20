from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collectors.codeql.corrections import (
    CorrectionError,
    apply_corrections,
    load_corrections,
)
from collectors.codeql.materializer import (
    MaterializationRun,
    materialize_call_graph,
    materialize_security_facts,
)
from collectors.codeql.model import (
    CallSiteRecord,
    CallTargetRecord,
    DataflowPathRecord,
    DefinitionRecord,
    GuardRecord,
    IdentityTransitionRecord,
    NormalizedRecord,
    ProgramValueRecord,
    SourceSpan,
)
from workspace.codeql_database import CodeQLDatabaseError, load_database_manifest
from workspace.codeql_runner import CodeQLRunnerError, run_queries


class CodeQLImportError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _directory_digest(directory: Path) -> str:
    return _digest(
        [
            (path.relative_to(directory).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(directory.glob("*.toml"))
        ]
    )


def _span(value: dict[str, object]) -> SourceSpan:
    return SourceSpan(
        repository_path=str(value["repository_path"]),
        source_path=str(value["source_path"]),
        start_line=int(value["start_line"]),
        start_column=int(value["start_column"]),
        end_line=int(value["end_line"]),
        end_column=int(value["end_column"]),
    )


def _record(value: dict[str, object]) -> NormalizedRecord:
    kind = str(value.get("record_type", ""))
    common = {
        "query_id": str(value["query_id"]),
        "query_version": str(value["query_version"]),
        "database_fingerprint": str(value["database_fingerprint"]),
    }
    if kind == "DefinitionRecord":
        return DefinitionRecord(
            language=str(value["language"]),
            package_name=str(value["package_name"]),
            declaring_type=str(value["declaring_type"]),
            callable_kind=str(value["callable_kind"]),
            callable_name=str(value["callable_name"]),
            erased_parameters=tuple(str(item) for item in value["erased_parameters"]),
            return_type=str(value["return_type"]),
            symbol_key=str(value["symbol_key"]),
            span=_span(dict(value["span"])),
            **common,
        )
    if kind == "CallSiteRecord":
        return CallSiteRecord(
            language=str(value["language"]),
            caller_symbol_key=str(value["caller_symbol_key"]),
            dispatch_kind=str(value["dispatch_kind"]),
            relation_kind=str(value["relation_kind"]),
            candidate_count=int(value["candidate_count"]),
            expression_text=str(value["expression_text"]),
            unresolved_reason=str(value["unresolved_reason"]),
            span=_span(dict(value["span"])),
            targets=tuple(
                CallTargetRecord(
                    callee_symbol_key=str(item["callee_symbol_key"]),
                    relation_kind=str(item["relation_kind"]),
                )
                for item in value["targets"]
            ),
            **common,
        )
    if kind == "DataflowPathRecord":
        return DataflowPathRecord(
            scenario=str(value["scenario"]),
            entry_symbol_key=str(value["entry_symbol_key"]),
            sink_symbol_key=str(value["sink_symbol_key"]),
            steps=tuple(
                ProgramValueRecord(
                    identity=str(item["identity"]),
                    symbol_key=str(item["symbol_key"]),
                    value_kind=str(item["value_kind"]),
                    ordinal=int(item["ordinal"]),
                    span=_span(dict(item["span"])),
                    parameter_index=(
                        None if item.get("parameter_index") is None else int(item["parameter_index"])
                    ),
                    declared_type=(
                        None if item.get("declared_type") is None else str(item["declared_type"])
                    ),
                )
                for item in value["steps"]
            ),
            **common,
        )
    if kind == "GuardRecord":
        return GuardRecord(
            owner_symbol_key=str(value["owner_symbol_key"]),
            guard_callable=str(value["guard_callable"]),
            sink_callable=str(value["sink_callable"]),
            relation_kind=str(value["relation_kind"]),
            guard_line=int(value["guard_line"]),
            sink_line=int(value["sink_line"]),
            source_path=str(value["source_path"]),
            **common,
        )
    if kind == "IdentityTransitionRecord":
        return IdentityTransitionRecord(
            owner_symbol_key=str(value["owner_symbol_key"]),
            clear_line=int(value["clear_line"]),
            restore_line=(None if value.get("restore_line") is None else int(value["restore_line"])),
            status=str(value["status"]),
            source_path=str(value["source_path"]),
            **common,
        )
    raise CodeQLImportError(f"unsupported normalized record type: {kind!r}")


def _load_records(paths: tuple[Path, ...]) -> tuple[NormalizedRecord, ...]:
    records: list[NormalizedRecord] = []
    for path in sorted(paths):
        try:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise CodeQLImportError(f"{path}:{number}: record is not an object")
                    records.append(_record(value))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            if isinstance(error, CodeQLImportError):
                raise
            raise CodeQLImportError(f"cannot load normalized records {path}: {error}") from error
    return tuple(records)


def _validate_database(codeql_database: Path, plan_path: Path) -> Any:
    manifest_path = codeql_database.parent / "manifest.json"
    manifest = load_database_manifest(manifest_path)
    if manifest.status != "verified" or manifest.language != "java-kotlin":
        raise CodeQLImportError("CodeQL database manifest is not verified java-kotlin")
    marker = codeql_database / "codeql-database.yml"
    if not marker.is_file():
        raise CodeQLImportError(f"CodeQL database marker is missing: {marker}")
    if hashlib.sha256(marker.read_bytes()).hexdigest() != manifest.database_marker_sha256:
        raise CodeQLImportError("CodeQL database marker digest mismatch")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    planned = {
        str(item["path"]): (str(item.get("revision", "")), str(item.get("inventory_sha256", "")))
        for item in plan.get("repositories", [])
        if item.get("enabled") and item.get("status") == "available"
    }
    observed = {
        item.path: (item.revision, item.inventory_sha256)
        for item in manifest.repositories
    }
    if set(planned) != set(observed):
        raise CodeQLImportError(
            "CodeQL repository set mismatch: "
            f"missing={sorted(set(planned) - set(observed))} "
            f"extra={sorted(set(observed) - set(planned))}"
        )
    mismatches = {
        path: (identity, observed.get(path))
        for path, identity in planned.items()
        if observed[path] != identity
    }
    if mismatches:
        raise CodeQLImportError(f"CodeQL source identity mismatch: {mismatches}")
    return manifest


def _insert_run(database: Path, manifest: Any, query_manifest: Any) -> tuple[str, str]:
    run_id = "CODEQL_RUN:" + _digest(
        [manifest.database_fingerprint, query_manifest.pack_lock_sha256]
    )
    evidence_id = "CODEQL_EVIDENCE:" + _digest(
        [run_id, [item.normalized_sha256 for item in query_manifest.queries]]
    )
    now = datetime.now(timezone.utc).isoformat()
    raw_hash = _digest([item.raw_sha256 for item in query_manifest.queries])
    normalized_hash = _digest([item.normalized_sha256 for item in query_manifest.queries])
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO extraction_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET status='running', completed_at=NULL
            """,
            (
                run_id, "call_dataflow", manifest.database_fingerprint,
                manifest.source_fingerprint, manifest.product, manifest.variant,
                json.dumps(list(manifest.build_targets)), manifest.codeql_version,
                manifest.extractor_version, query_manifest.pack_lock_sha256,
                manifest.observed_java_files + manifest.observed_kotlin_files,
                sum(item.row_count for item in query_manifest.queries), "running", now,
                None, json.dumps({"query_count": len(query_manifest.queries)}),
            ),
        )
        connection.execute(
            """
            INSERT INTO extraction_evidence VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(evidence_id) DO UPDATE SET
              raw_result_hash=excluded.raw_result_hash,
              content_hash=excluded.content_hash
            """,
            (
                evidence_id, run_id, "android-context/java-kotlin-call-dataflow",
                "CallDataflowBundle", "1", raw_hash, "query-run-manifest.json",
                manifest.database_fingerprint, normalized_hash,
                json.dumps({"queries": [item.query_id for item in query_manifest.queries]}),
            ),
        )
    return run_id, evidence_id


def import_codeql_facts(
    *,
    database: Path,
    codeql_database: Path,
    codeql_bin: Path,
    pack: Path,
    cache_dir: Path,
    plan: Path,
    corrections_dir: Path,
    report: Path,
    correction_report: Path,
) -> dict[str, object]:
    if os.environ.get("FORCE_CODEQL_VALIDATION_FAILURE") == "1":
        raise CodeQLImportError("forced CodeQL validation failure")
    manifest = _validate_database(codeql_database, plan)
    query_manifest = run_queries(codeql_database, pack, cache_dir, codeql_bin)
    records = _load_records(tuple(Path(item.normalized_path) for item in query_manifest.queries))
    run_id, evidence_id = _insert_run(database, manifest, query_manifest)
    run = MaterializationRun(run_id, evidence_id, manifest.source_fingerprint)
    call_report = materialize_call_graph(database, records, run)
    security_report = materialize_security_facts(database, records, run)
    corrections = load_corrections(corrections_dir)
    applications = apply_corrections(
        database,
        corrections,
        source_revision=manifest.source_fingerprint,
        run_id=run_id,
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE extraction_run SET status='complete', completed_at=? WHERE run_id=?",
            (completed_at, run_id),
        )
    correction_payload = {
        "run_id": run_id,
        "correction_directory_sha256": _directory_digest(corrections_dir),
        "applications": [asdict(item) for item in applications.applications],
    }
    _atomic_json(correction_report, correction_payload)
    payload: dict[str, object] = {
        "status": "complete",
        "run_id": run_id,
        "database_manifest": manifest.to_dict(),
        "query_manifest": query_manifest.to_dict(),
        "call_materialization": asdict(call_report),
        "security_materialization": asdict(security_report),
        "corrections": correction_payload,
    }
    _atomic_json(report, payload)
    return payload


def write_skipped(report: Path, correction_report: Path) -> None:
    _atomic_json(
        report,
        {
            "status": "skipped",
            "reason": "codeql_database_not_provided",
            "call_graph": "degraded",
            "interprocedural_dataflow": "degraded",
        },
    )
    _atomic_json(correction_report, {"status": "skipped", "applications": []})


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import CodeQL call/dataflow facts")
    parser.add_argument("--skip", action="store_true")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--codeql-db", type=Path)
    parser.add_argument("--codeql-bin", type=Path)
    parser.add_argument("--pack", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--corrections-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--correction-report", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        if args.skip:
            write_skipped(args.report, args.correction_report)
        else:
            required = {
                "db": args.db,
                "codeql_db": args.codeql_db,
                "codeql_bin": args.codeql_bin,
                "pack": args.pack,
                "cache_dir": args.cache_dir,
                "plan": args.plan,
                "corrections_dir": args.corrections_dir,
            }
            missing = sorted(key for key, value in required.items() if value is None)
            if missing:
                raise CodeQLImportError(f"missing required arguments: {missing}")
            import_codeql_facts(
                database=args.db,
                codeql_database=args.codeql_db,
                codeql_bin=args.codeql_bin,
                pack=args.pack,
                cache_dir=args.cache_dir,
                plan=args.plan,
                corrections_dir=args.corrections_dir,
                report=args.report,
                correction_report=args.correction_report,
            )
    except (
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        CodeQLImportError,
        CodeQLDatabaseError,
        CodeQLRunnerError,
        CorrectionError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
