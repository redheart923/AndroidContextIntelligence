from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from collectors.service.service_registration_importer import (
    ConstantResolver,
    DbTypeIndex,
    SourceScanMetrics,
    build_fact,
    find_registration_calls,
    import_fact,
    scan_sources,
)
from graph.writer import GraphWriter
from workspace.pipeline import (
    load_plan,
    repositories_for,
    scan_paths,
    source_allowed,
)


def run_service_pipeline(
    plan_path: Path,
    database: Path,
    report_path: Path,
    cache_dir: Path | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    plan = load_plan(plan_path)
    root = Path(plan["aosp_root"])
    sources = []
    source_repo: dict[str, str] = {}
    seen_paths: set[Path] = set()
    defaults = plan.get("default_exclude", [])
    metrics = SourceScanMetrics()
    phase_started = time.perf_counter()
    for repository in repositories_for(
        plan,
        "java",
        "service_registration",
    ):
        for scan_root in scan_paths(root, repository):
            items = scan_sources(
                scan_root,
                root,
                cache_dir,
                metrics,
                path_filter=lambda path: source_allowed(
                    root,
                    repository,
                    path,
                    defaults,
                ),
            )
            for item in items:
                resolved_path = item.path.resolve()
                if resolved_path in seen_paths:
                    continue
                seen_paths.add(resolved_path)
                sources.append(item)
                source_repo[item.source_path] = repository["name"]
    source_scan_seconds = time.perf_counter() - phase_started

    phase_started = time.perf_counter()
    constants = ConstantResolver(sources)
    constant_index_seconds = time.perf_counter() - phase_started
    phase_started = time.perf_counter()
    with sqlite3.connect(database) as connection:
        types = DbTypeIndex(connection)
    type_index_seconds = time.perf_counter() - phase_started
    phase_started = time.perf_counter()
    facts = [
        build_fact(source, call, constants, types)
        for source in sources
        for call in find_registration_calls(source)
    ]
    fact_build_seconds = time.perf_counter() - phase_started
    phase_started = time.perf_counter()
    writer = GraphWriter(database)
    try:
        for fact in facts:
            import_fact(writer, fact, types)
    finally:
        writer.close()
    graph_write_seconds = time.perf_counter() - phase_started

    summary: dict[str, int] = defaultdict(int)
    for fact in facts:
        summary[fact.api] += 1
        summary[f"status:{fact.resolution_status}"] += 1
        summary[f"kind:{fact.service_kind}"] += 1
        if fact.is_test_source:
            summary["test_sources"] += 1
    elapsed = time.perf_counter() - started
    report: dict[str, object] = {
        "summary": dict(sorted(summary.items())),
        "performance": {
            "elapsed_seconds": elapsed,
            "scanned_files": metrics.scanned_files,
            "candidate_files": metrics.candidate_files,
            "excluded_candidates": metrics.excluded_candidates,
            "models_loaded": len(sources),
            "cache_hits": metrics.cache_hits,
            "cache_misses": metrics.cache_misses,
            "phases": {
                "source_scan_seconds": source_scan_seconds,
                "constant_index_seconds": constant_index_seconds,
                "type_index_seconds": type_index_seconds,
                "fact_build_seconds": fact_build_seconds,
                "graph_write_seconds": graph_write_seconds,
            },
        },
        "registrations": [
            {
                "registration_id": fact.registration_id,
                "api": fact.api,
                "resolved_key": fact.resolved_key,
                "resolved_instance_type": fact.resolved_instance_type,
                "resolution_status": fact.resolution_status,
                "source_path": fact.source_path,
                "repository": source_repo.get(fact.source_path),
                "line": fact.line,
            }
            for fact in facts
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    resolved = sum(fact.resolution_status == "resolved" for fact in facts)
    print(
        f"Service registrations: {len(facts)}; resolved: {resolved}; "
        f"candidates: {metrics.candidate_files}/{metrics.scanned_files}; "
        f"excluded candidates: {metrics.excluded_candidates}; "
        f"cache hits: {metrics.cache_hits}; elapsed: {elapsed:.3f}s"
    )
    return report


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parsed = parser.parse_args(arguments)
    run_service_pipeline(
        parsed.plan,
        parsed.db,
        parsed.report,
        parsed.cache_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
