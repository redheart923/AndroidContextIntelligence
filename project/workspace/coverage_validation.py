from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path


class RuntimeCoverageError(RuntimeError):
    pass


TYPED_EVIDENCE_TABLES = {
    "semantic_definition",
    "call_site",
    "call_target",
    "dataflow_path",
    "security_trace",
}


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _source_predicate(repository_path: str) -> tuple[str, tuple[str, str]]:
    normalized = repository_path.replace("\\", "/").rstrip("/")
    return (
        "(REPLACE(COALESCE(source_path, ''), '\\', '/') = ? "
        "OR REPLACE(COALESCE(source_path, ''), '\\', '/') LIKE ?)",
        (normalized, normalized + "/%"),
    )


def _evidence_count(
    connection: sqlite3.Connection,
    repository_path: str,
    language: str,
    evidence: str,
) -> int:
    source_sql, source_args = _source_predicate(repository_path)
    kind, separator, value = evidence.partition(":")
    if not separator or not value:
        raise RuntimeCoverageError(f"invalid evidence contract: {evidence}")
    suffixes = {
        "java": ".java",
        "kotlin": ".kt",
        "xml": ".xml",
        "aidl": ".aidl",
    }
    suffix = suffixes.get(language)
    suffix_sql = (
        " AND LOWER(REPLACE(COALESCE(source_path, ''), '\\', '/')) LIKE ?"
        if suffix else ""
    )
    suffix_args: tuple[str, ...] = (f"%{suffix}",) if suffix else ()
    if kind == "node_type":
        query = (
            f"SELECT COUNT(*) FROM node WHERE node_type=? AND {source_sql}"
            + suffix_sql
        )
        args: tuple[object, ...] = (value, *source_args, *suffix_args)
    elif kind == "node_type_prefix":
        query = (
            f"SELECT COUNT(*) FROM node WHERE node_type LIKE ? AND {source_sql}"
            + suffix_sql
        )
        args = (value + "%", *source_args, *suffix_args)
    elif kind in {"edge_type", "edge_type_any_source"}:
        query = f"SELECT COUNT(*) FROM edge WHERE edge_type=? AND {source_sql}"
        args = (value, *source_args)
        if kind == "edge_type":
            query += suffix_sql
            args = (*args, *suffix_args)
    elif kind == "typed_table":
        if value not in TYPED_EVIDENCE_TABLES:
            raise RuntimeCoverageError(
                f"unsupported typed evidence table: {value}"
            )
        query = (
            f'SELECT COUNT(*) FROM "{value}" WHERE repository=? AND '
            + source_sql
            + suffix_sql
        )
        args = (repository_path, *source_args, *suffix_args)
    else:
        raise RuntimeCoverageError(f"unsupported evidence contract: {evidence}")
    return int(connection.execute(query, args).fetchone()[0])


def evaluate_runtime_coverage(
    plan: dict[str, object],
    database: Path,
) -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    try:
        connection = sqlite3.connect(database)
        for raw_task in plan.get("tasks", []):
            task = dict(raw_task)
            planned_status = str(task.get("status", "unsupported"))
            evidence = tuple(str(item) for item in task.get("expected_evidence", []))
            observed = 0
            reasons: list[str] = []
            status = planned_status
            if planned_status == "scheduled":
                if not evidence:
                    status = "degraded"
                    reasons.append("missing_required_evidence_contract")
                else:
                    observed = sum(
                        _evidence_count(
                            connection,
                            str(task.get("repository_path", "")),
                            str(task.get("language", "")),
                            item,
                        )
                        for item in evidence
                    )
                    if observed == 0:
                        status = "degraded"
                        reasons.append("required_evidence_not_observed")
                    else:
                        status = "supported"
            report.append(
                {
                    "repository": task.get("repository"),
                    "repository_path": task.get("repository_path"),
                    "language": task.get("language"),
                    "capability": task.get("capability"),
                    "parser": task.get("parser"),
                    "planned_status": planned_status,
                    "status": status,
                    "quality": task.get("quality"),
                    "files": int(task.get("files", 0)),
                    "expected_evidence": list(evidence),
                    "observed_count": observed,
                    "degradation_reasons": reasons,
                }
            )
    except sqlite3.Error as error:
        raise RuntimeCoverageError(f"cannot inspect graph evidence: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()
    return report


def validate_runtime_coverage(
    plan: dict[str, object],
    database: Path,
    report_path: Path,
) -> list[dict[str, object]]:
    report = evaluate_runtime_coverage(plan, database)
    _atomic_json(report_path, report)
    strict_capabilities = plan.get("strict_capabilities")
    if strict_capabilities is None:
        legacy = plan.get("strict_capability")
        strict_capabilities = [legacy] if legacy else []
    selected = {str(item) for item in strict_capabilities if item}
    gaps = [
        item for item in report
        if item["planned_status"] == "scheduled"
        and item["status"] != "supported"
        and (
            not selected
            or item["capability"] in selected
        )
    ]
    if bool(plan.get("strict")) and gaps:
        sample = ", ".join(
            f"{item['repository']}:{item['language']}:{item['capability']}"
            for item in gaps[:8]
        )
        raise RuntimeCoverageError(f"runtime coverage gaps: {sample}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        report = validate_runtime_coverage(plan, args.db, args.report)
    except (OSError, json.JSONDecodeError, RuntimeCoverageError) as error:
        print(f"ERROR: {error}")
        return 1
    counts: dict[str, int] = {}
    for item in report:
        status = str(item["status"])
        counts[status] = counts.get(status, 0) + 1
    print(
        "Runtime coverage: "
        + "; ".join(f"{key}: {counts[key]}" for key in sorted(counts))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
