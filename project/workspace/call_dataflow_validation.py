from __future__ import annotations

import argparse
import json
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CallDataflowValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationReport:
    metrics: dict[str, int | float]
    warnings: tuple[str, ...]
    strong_evidence: dict[str, str]


def _scalar(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def _validate_foreign_keys(connection: sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise CallDataflowValidationError(
            f"foreign-key violations: {violations[:5]}"
        )


def _validate_call_endpoints(connection: sqlite3.Connection) -> None:
    invalid = connection.execute(
        """
        SELECT ct.call_site_id, ct.callee_method_id
        FROM call_target ct
        JOIN call_site cs ON cs.call_site_id=ct.call_site_id
        WHERE cs.resolution_status='resolved'
          AND (
            NOT EXISTS (
              SELECT 1 FROM semantic_definition caller
              WHERE caller.logical_method_id=cs.caller_method_id
                AND caller.resolution_status='unique'
            )
            OR NOT EXISTS (
              SELECT 1 FROM semantic_definition callee
              WHERE callee.logical_method_id=ct.callee_method_id
                AND callee.resolution_status='unique'
            )
          )
        ORDER BY ct.call_site_id, ct.callee_method_id
        """
    ).fetchall()
    if invalid:
        raise CallDataflowValidationError(
            f"accepted target has ambiguous endpoint: {invalid[:5]}"
        )
    inconsistent = connection.execute(
        """
        SELECT
          cs.call_site_id,
          cs.resolution_status,
          cs.candidate_count,
          COUNT(ct.callee_method_id) AS accepted_target_count
        FROM call_site cs
        LEFT JOIN call_target ct ON ct.call_site_id=cs.call_site_id
        GROUP BY cs.call_site_id, cs.resolution_status, cs.candidate_count
        HAVING (
          cs.resolution_status='resolved'
          AND (
            cs.candidate_count < 1
            OR COUNT(ct.callee_method_id) != cs.candidate_count
            OR (
              SUM(CASE WHEN ct.relation_kind='must' THEN 1 ELSE 0 END) > 0
              AND (
                cs.candidate_count != 1
                OR SUM(CASE WHEN ct.relation_kind='may' THEN 1 ELSE 0 END) > 0
              )
            )
          )
        )
        OR (
          cs.resolution_status='ambiguous'
          AND (cs.candidate_count < 1 OR COUNT(ct.callee_method_id) != 0)
        )
        OR (
          cs.resolution_status IN ('unresolved','unsupported')
          AND COUNT(ct.callee_method_id) != 0
        )
        """
    ).fetchall()
    if inconsistent:
        raise CallDataflowValidationError(
            f"call-site classification is inconsistent: {inconsistent[:5]}"
        )


def _validate_paths(connection: sqlite3.Connection) -> None:
    for path_id, expected_count, source_id, sink_id in connection.execute(
        """
        SELECT path_id, step_count, source_value_id, sink_value_id
        FROM dataflow_path
        WHERE status IN ('accepted', 'active', 'resolved')
        ORDER BY path_id
        """
    ):
        rows = connection.execute(
            """
            SELECT ordinal, value_id, source_path, line_start, line_end
            FROM dataflow_step WHERE path_id=? ORDER BY ordinal
            """,
            (path_id,),
        ).fetchall()
        ordinals = [int(row[0]) for row in rows]
        if ordinals != list(range(len(rows))) or len(rows) != int(expected_count):
            raise CallDataflowValidationError(
                f"dataflow path {path_id} has non-contiguous steps: {ordinals}"
            )
        if not rows or rows[0][1] != source_id or rows[-1][1] != sink_id:
            raise CallDataflowValidationError(
                f"dataflow path {path_id} source/sink evidence mismatch"
            )
        if any(
            not row[2]
            or row[3] is None
            or row[4] is None
            or int(row[3]) < 1
            or int(row[4]) < int(row[3])
            for row in rows
        ):
            raise CallDataflowValidationError(
                f"dataflow path {path_id} has invalid source span"
            )


def _validate_security_traces(connection: sqlite3.Connection) -> None:
    for trace_id, expected_guards, expected_identity, status in connection.execute(
        """
        SELECT trace_id, guard_count, identity_transition_count, status
        FROM security_trace ORDER BY trace_id
        """
    ):
        rows = connection.execute(
            "SELECT ordinal, step_kind FROM security_trace_step WHERE trace_id=? ORDER BY ordinal",
            (trace_id,),
        ).fetchall()
        ordinals = [int(row[0]) for row in rows]
        if ordinals != list(range(len(rows))):
            raise CallDataflowValidationError(
                f"security trace {trace_id} has non-contiguous steps: {ordinals}"
            )
        guard_count = sum(row[1] == "guard" for row in rows)
        identity_count = sum(row[1] in {"identity_clear", "identity_restore"} for row in rows)
        if guard_count != int(expected_guards) or identity_count != int(expected_identity):
            raise CallDataflowValidationError(
                f"security trace {trace_id} count mismatch"
            )
        clears = sum(row[1] == "identity_clear" for row in rows)
        restores = sum(row[1] == "identity_restore" for row in rows)
        if status not in {"identity_unpaired", "diagnostic"} and clears != restores:
            raise CallDataflowValidationError(
                f"security trace {trace_id} has unpaired identity transitions"
            )


def _validate_content_hashes(connection: sqlite3.Connection) -> None:
    for table, fingerprint_column in (
        ("semantic_definition", "content_hash"),
        ("call_site", "content_hash"),
        ("program_value", "content_hash"),
        ("dataflow_path", "path_fingerprint"),
        ("security_trace", "trace_fingerprint"),
    ):
        duplicates = connection.execute(
            f"SELECT {fingerprint_column}, COUNT(*) FROM {table} "
            f"GROUP BY {fingerprint_column} HAVING COUNT(*) > 1 LIMIT 5"
        ).fetchall()
        if duplicates:
            raise CallDataflowValidationError(
                f"duplicate content hashes in {table}: {duplicates}"
            )


def _correction_gaps(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(correction_id), str(status))
        for correction_id, status in connection.execute(
        """
        SELECT correction_id, application_status
        FROM correction_application
        WHERE application_status != 'applied'
        ORDER BY correction_id
        """
        )
    )


def _call_evidence_count(
    connection: sqlite3.Connection, evidence: dict[str, Any]
) -> int:
    required = (
        "caller_symbol_key",
        "callee_symbol_key",
        "relation_kind",
        "source_path",
    )
    missing = [key for key in required if not evidence.get(key)]
    if missing:
        raise CallDataflowValidationError(
            f"strong evidence {evidence.get('id', '<unknown>')} missing fields: {missing}"
        )
    if evidence["relation_kind"] not in {"must", "may"}:
        raise CallDataflowValidationError(
            f"strong evidence {evidence.get('id', '<unknown>')} has invalid relation_kind"
        )
    return _scalar(
        connection,
        """
        SELECT COUNT(DISTINCT cs.call_site_id)
        FROM call_site cs
        JOIN call_target ct ON ct.call_site_id=cs.call_site_id
        JOIN semantic_definition caller
          ON caller.logical_method_id=cs.caller_method_id
         AND caller.resolution_status='unique'
        JOIN semantic_definition callee
          ON callee.logical_method_id=ct.callee_method_id
         AND callee.resolution_status='unique'
        WHERE caller.semantic_symbol_key=?
          AND callee.semantic_symbol_key=?
          AND ct.relation_kind=?
          AND cs.source_path=?
        """,
        (
            str(evidence["caller_symbol_key"]),
            str(evidence["callee_symbol_key"]),
            str(evidence["relation_kind"]),
            str(evidence["source_path"]),
        ),
    )


def _security_trace_evidence_count(
    connection: sqlite3.Connection, evidence: dict[str, Any]
) -> int:
    required = ("entry_symbol_key", "scenario_id", "status", "source_path")
    missing = [key for key in required if not evidence.get(key)]
    if missing:
        raise CallDataflowValidationError(
            f"strong evidence {evidence.get('id', '<unknown>')} missing fields: {missing}"
        )
    trace_ids = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT trace.trace_id
            FROM security_trace trace
            JOIN semantic_definition entry
              ON entry.logical_method_id=trace.entry_method_id
             AND entry.resolution_status='unique'
            WHERE entry.semantic_symbol_key=?
              AND trace.scenario_id=?
              AND trace.status=?
              AND trace.source_path=?
              AND trace.guard_count>=?
              AND trace.identity_transition_count>=?
            ORDER BY trace.trace_id
            """,
            (
                str(evidence["entry_symbol_key"]),
                str(evidence["scenario_id"]),
                str(evidence["status"]),
                str(evidence["source_path"]),
                int(evidence.get("minimum_guard_count", 0)),
                int(evidence.get("minimum_identity_transition_count", 0)),
            ),
        )
    ]
    required_steps = {str(item) for item in evidence.get("required_step_kinds", [])}
    if not required_steps:
        return len(trace_ids)
    matched = 0
    for trace_id in trace_ids:
        actual_steps = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT step_kind FROM security_trace_step WHERE trace_id=?",
                (trace_id,),
            )
        }
        matched += required_steps.issubset(actual_steps)
    return matched


def _strong_evidence(
    connection: sqlite3.Connection, config_path: Path | None
) -> dict[str, str]:
    if config_path is None:
        config_path = Path(__file__).resolve().parents[1] / "config/codeql.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        evidence_items = config["acceptance"]["strong_evidence"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise CallDataflowValidationError(
            f"cannot read strong-evidence configuration: {error}"
        ) from error
    if not isinstance(evidence_items, list) or not evidence_items:
        raise CallDataflowValidationError(
            "strong-evidence configuration must contain at least one entry"
        )
    result: dict[str, str] = {}
    for item in evidence_items:
        if not isinstance(item, dict) or not item.get("id") or not item.get("kind"):
            raise CallDataflowValidationError(
                "each strong-evidence entry requires id and kind"
            )
        evidence_id = str(item["id"])
        if evidence_id in result:
            raise CallDataflowValidationError(
                f"duplicate strong-evidence id: {evidence_id}"
            )
        kind = str(item["kind"])
        if kind in {"call", "absent_call"}:
            count = _call_evidence_count(connection, item)
            passed = count > 0 if kind == "call" else count == 0
        elif kind in {"security_trace", "absent_security_trace"}:
            count = _security_trace_evidence_count(connection, item)
            passed = count > 0 if kind == "security_trace" else count == 0
        else:
            raise CallDataflowValidationError(
                f"strong evidence {evidence_id} has unsupported kind: {kind}"
            )
        result[evidence_id] = "pass" if passed else "missing"
    missing = [name for name, status in result.items() if status != "pass"]
    if missing:
        raise CallDataflowValidationError(
            f"missing AOSP strong evidence: {missing}"
        )
    return result


def validate_call_dataflow(
    database: Path,
    *,
    require_aosp_evidence: bool,
    config_path: Path | None = None,
) -> ValidationReport:
    if not database.is_file():
        raise CallDataflowValidationError(f"database does not exist: {database}")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA query_only=ON")
        _validate_foreign_keys(connection)
        _validate_call_endpoints(connection)
        _validate_paths(connection)
        _validate_security_traces(connection)
        _validate_content_hashes(connection)
        correction_gaps = _correction_gaps(connection)
        if require_aosp_evidence and correction_gaps:
            raise CallDataflowValidationError(
                f"non-active corrections block publication: {correction_gaps[:5]}"
            )
        eligible = _scalar(
            connection,
            "SELECT COUNT(*) FROM semantic_definition WHERE resolution_status != 'synthetic'",
        )
        unique = _scalar(
            connection,
            "SELECT COUNT(*) FROM semantic_definition WHERE resolution_status='unique'",
        )
        reconciliation = 100.0 if eligible == 0 else unique * 100.0 / eligible
        if require_aosp_evidence:
            try:
                minimum = float(
                    tomllib.loads(
                        (config_path or Path(__file__).resolve().parents[1] / "config/codeql.toml")
                        .read_text(encoding="utf-8")
                    )["acceptance"]["minimum_identity_reconciliation_percent"]
                )
            except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
                raise CallDataflowValidationError(
                    f"cannot read reconciliation threshold: {error}"
                ) from error
            if reconciliation < minimum:
                raise CallDataflowValidationError(
                    f"identity reconciliation {reconciliation:.2f}% is below {minimum:.2f}%"
                )
            evidence = _strong_evidence(connection, config_path)
        else:
            evidence = {}
        metrics: dict[str, int | float] = {
            "eligible_definitions": eligible,
            "unique_definitions": unique,
            "reconciliation_percent": reconciliation,
            "call_sites": _scalar(connection, "SELECT COUNT(*) FROM call_site"),
            "accepted_targets": _scalar(connection, "SELECT COUNT(*) FROM call_target"),
            "dataflow_paths": _scalar(connection, "SELECT COUNT(*) FROM dataflow_path"),
            "security_traces": _scalar(connection, "SELECT COUNT(*) FROM security_trace"),
        }
        warnings = []
        if not eligible:
            warnings.append("no eligible semantic definitions")
        stale_count = sum(status == "stale" for _, status in correction_gaps)
        if stale_count:
            warnings.append(f"stale corrections: {stale_count}")
        return ValidationReport(metrics, tuple(warnings), evidence)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate call/dataflow semantic facts")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--require-aosp-evidence", action="store_true")
    args = parser.parse_args(arguments)
    try:
        report = validate_call_dataflow(
            args.db,
            require_aosp_evidence=args.require_aosp_evidence,
            config_path=args.config,
        )
    except CallDataflowValidationError as error:
        print(f"ERROR: {error}")
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            {
                "metrics": report.metrics,
                "warnings": list(report.warnings),
                "strong_evidence": report.strong_evidence,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "call_dataflow_validation: PASS; "
        f"call_sites={report.metrics['call_sites']}; "
        f"paths={report.metrics['dataflow_paths']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
