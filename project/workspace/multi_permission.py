from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from collectors.permission.materializer import materialize_permission_facts
from collectors.permission.model import ParseOutcome
from collectors.permission.report import PermissionReport
from collectors.permission.source_permission_scanner import (
    load_method_ranges,
    scan_permission_source,
)
from collectors.permission.xml_permission_importer import (
    is_permission_xml_candidate,
    parse_permission_xml,
)
from graph.writer import GraphWriter
from workspace.pipeline import load_plan, scan_paths, source_allowed


def _repository(plan: dict, task: dict) -> dict:
    return next(
        item for item in plan["repositories"]
        if item["name"] == task["repository"]
    )


def _files(plan: dict, repo: dict, suffix: str) -> tuple[Path, ...]:
    root = Path(plan["aosp_root"])
    defaults = plan.get("default_exclude", [])
    found: set[Path] = set()
    for scan_root in scan_paths(root, repo):
        candidates = (scan_root,) if scan_root.is_file() else scan_root.rglob(f"*{suffix}")
        for path in candidates:
            if path.is_file() and path.suffix == suffix and source_allowed(root, repo, path, defaults):
                found.add(path)
    return tuple(sorted(found, key=lambda item: str(item)))


def _source_path(plan: dict, path: Path) -> str:
    root = Path(plan["aosp_root"])
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _merge(outcomes: list[ParseOutcome], counters: Counter[str]) -> ParseOutcome:
    return ParseOutcome(
        facts=tuple(fact for outcome in outcomes for fact in outcome.facts),
        diagnostics=tuple(item for outcome in outcomes for item in outcome.diagnostics),
        counters=dict(counters),
    )


def scan_xml_repository(task: dict, *, plan: dict, database: Path) -> ParseOutcome:
    del database
    repo = _repository(plan, task)
    revision = repo.get("revision")
    outcomes: list[ParseOutcome] = []
    counters: Counter[str] = Counter()
    for path in _files(plan, repo, ".xml"):
        counters["files_scanned.xml"] += 1
        if not is_permission_xml_candidate(path):
            continue
        outcome = parse_permission_xml(
            path,
            repository=repo["name"],
            source_path=_source_path(plan, path),
            source_revision=revision or "unknown",
        )
        outcomes.append(outcome)
        counters.update(outcome.counters)
        counters["files_scanned.xml"] -= outcome.counters.get("files_scanned.xml", 0)
    return _merge(outcomes, counters)


def scan_source_repository(task: dict, *, plan: dict, database: Path) -> ParseOutcome:
    repo = _repository(plan, task)
    language = task["language"]
    suffix = ".kt" if language == "kotlin" else ".java"
    outcomes: list[ParseOutcome] = []
    counters: Counter[str] = Counter()
    with sqlite3.connect(database) as connection:
        for path in _files(plan, repo, suffix):
            source_path = _source_path(plan, path)
            outcome = scan_permission_source(
                path,
                repository=repo["name"],
                source_path=source_path,
                source_revision=repo.get("revision") or "unknown",
                language=language,
                methods=load_method_ranges(connection, source_path),
            )
            outcomes.append(outcome)
            counters.update(outcome.counters)
    return _merge(outcomes, counters)


PARSERS = {
    "xml": scan_xml_repository,
    "java": scan_source_repository,
    "kotlin": scan_source_repository,
}


def atomic_write_json(path: Path, value: object) -> None:
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


def permission_tasks(plan: dict) -> tuple[dict, ...]:
    return tuple(
        sorted(
            (
                task for task in plan.get("tasks", [])
                if task.get("capability") == "permission_semantics"
                and task.get("status") == "scheduled"
            ),
            key=lambda task: (
                str(task.get("repository")),
                str(task.get("language")),
            ),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    plan = load_plan(args.plan)
    report = PermissionReport()
    for repo in plan.get("repositories", []):
        if repo.get("enabled") and repo.get("status") == "available":
            report.register_repository(repo["name"], repo.get("revision"))

    had_task_failure = False
    for task in permission_tasks(plan):
        task_parser = PARSERS.get(task.get("language"))
        if task_parser is None:
            report.add_task_failure(task, "missing_scheduled_parser")
            had_task_failure = True
            continue
        try:
            report.add_outcome(task_parser(task, plan=plan, database=args.db))
        except Exception as error:
            failed = dict(task)
            failed["error"] = f"{type(error).__name__}: {error}"
            report.add_task_failure(failed, "parser_exception")
            had_task_failure = True

    writer = GraphWriter(args.db)
    try:
        summary = materialize_permission_facts(writer, report.sorted_facts())
    finally:
        writer.close()
    report.add_outcome(ParseOutcome(diagnostics=summary.diagnostics))
    payload = report.to_dict()
    atomic_write_json(args.report, payload)

    strict = bool(plan.get("strict"))
    strict_findings = bool(payload["malformed_xml"] or payload["task_failures"])
    if strict and strict_findings:
        return 2
    if had_task_failure:
        return 1
    print(
        "Permission semantics: "
        f"{sum(summary.edge_counts.values())} edges; "
        f"{len(payload['task_failures'])} task failures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
