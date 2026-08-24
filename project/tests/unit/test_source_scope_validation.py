from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from workspace.source_scope_validation import (
    SourceScopeError,
    bind_publication_scope_fingerprint,
    main,
    scope_report_fingerprint,
    validate_post_import,
    validate_preflight,
    write_scope_report,
)


def plan_fixture() -> dict[str, object]:
    return {
        "aosp_root": "/workspace",
        "analysis_scope": "partial",
        "full_aosp_coverage": False,
        "strict": False,
        "strict_capability": None,
        "repositories": [
            {
                "name": "demo/repo",
                "path": "demo/repo",
                "enabled": True,
                "status": "available",
                "revision": None,
                "revision_state": "non_git",
                "inventory_sha256": "a" * 64,
                "inventory_file_count": 1,
            }
        ],
        "inventories": [
            {"repository": "demo/repo", "counts": {"java": 1}}
        ],
        "tasks": [
            {
                "repository": "demo/repo",
                "repository_path": "demo/repo",
                "language": "java",
                "capability": "symbols",
                "parser": "java_symbol_importer",
                "status": "scheduled",
                "files": 1,
            }
        ],
    }


def capability_report(status: str = "supported") -> list[dict[str, object]]:
    return [
        {
            "repository": "demo/repo",
            "repository_path": "demo/repo",
            "language": "java",
            "capability": "symbols",
            "planned_status": "scheduled",
            "status": status,
            "files": 1,
        }
    ]


def provenance_fixture() -> dict[str, object]:
    return {
        "repositories": [
            {
                "name": "demo/repo",
                "path": "demo/repo",
                "revision": None,
                "state": "non_git",
                "inventory_sha256": "a" * 64,
                "file_count": 1,
                "matches_plan": True,
            }
        ]
    }


def graph_database(tmp_path: Path, *, source_path: str = "demo/repo/Demo.java") -> Path:
    database = tmp_path / "graph.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE node (
          node_id TEXT PRIMARY KEY,
          node_type TEXT NOT NULL,
          properties_json TEXT NOT NULL DEFAULT '{}',
          source_path TEXT
        );
        CREATE TABLE edge (
          edge_id TEXT PRIMARY KEY,
          edge_type TEXT NOT NULL,
          from_node_id TEXT NOT NULL,
          to_node_id TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO node VALUES (?, ?, ?, ?)",
        (
            "JAVA_CLASS:demo.Demo",
            "JAVA_CLASS",
            json.dumps({"repository": "demo/repo"}),
            source_path,
        ),
    )
    connection.commit()
    connection.close()
    return database


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda plan: plan.update(repositories=[]),
            "no_enabled_repository",
        ),
        (
            lambda plan: plan["repositories"][0].update(status="missing"),
            "missing_enabled_repository",
        ),
        (
            lambda plan: plan["repositories"][0].update(
                inventory_file_count=0
            ),
            "empty_repository_inventory",
        ),
        (
            lambda plan: plan.update(inventories=[]),
            "no_recognized_language",
        ),
        (
            lambda plan: plan.update(tasks=[]),
            "no_scheduled_parser",
        ),
        (
            lambda plan: plan["repositories"][0].update(
                inventory_sha256=None
            ),
            "scope_repository_mismatch",
        ),
        (
            lambda plan: plan["repositories"][0].update(
                revision_state="not_inspected"
            ),
            "scope_repository_mismatch",
        ),
    ],
)
def test_preflight_rejects_unpublishable_scope(mutate, reason: str) -> None:
    plan = plan_fixture()
    mutate(plan)

    with pytest.raises(SourceScopeError, match=reason):
        validate_preflight(plan)


def test_preflight_accepts_non_git_repository_with_inventory_identity() -> None:
    report = validate_preflight(plan_fixture())

    assert report["status"] == "preflight_passed"
    assert report["analysis_scope"] == "partial"
    assert report["scheduled_task_count"] == 1
    assert report["enabled_repositories"][0]["revision"] is None
    assert report["enabled_repositories"][0]["revision_state"] == "non_git"


def test_preflight_rejects_duplicate_repository_name() -> None:
    plan = plan_fixture()
    duplicate = deepcopy(plan["repositories"][0])
    duplicate["path"] = "other/path"
    plan["repositories"].append(duplicate)

    with pytest.raises(SourceScopeError, match="scope_repository_mismatch"):
        validate_preflight(plan)


def test_partial_post_import_does_not_require_local_services(tmp_path: Path) -> None:
    payload = validate_post_import(
        plan_fixture(),
        graph_database(tmp_path),
        capability_report(),
        provenance_fixture(),
        "fixture-build",
    )

    assert payload["build_id"] == "fixture-build"
    assert payload["status"] == "passed"
    assert payload["source_backed_node_count"] == 1
    assert payload["capability_counts"] == {"supported": 1}


def test_post_import_rejects_empty_or_wrong_repository_graph(tmp_path: Path) -> None:
    database = graph_database(tmp_path, source_path="other/repo/Demo.java")

    with pytest.raises(SourceScopeError, match="no_source_backed_node"):
        validate_post_import(
            plan_fixture(),
            database,
            capability_report(),
            provenance_fixture(),
            "fixture-build",
        )


def test_post_import_rejects_unexecuted_scheduled_capability(tmp_path: Path) -> None:
    report = capability_report(status="scheduled")

    with pytest.raises(SourceScopeError, match="capability_not_executed"):
        validate_post_import(
            plan_fixture(),
            graph_database(tmp_path),
            report,
            provenance_fixture(),
            "fixture-build",
        )


def test_post_import_rejects_degraded_strict_capability(tmp_path: Path) -> None:
    plan = plan_fixture()
    plan["strict_capability"] = "symbols"

    with pytest.raises(SourceScopeError, match="strict_capability_gap"):
        validate_post_import(
            plan,
            graph_database(tmp_path),
            capability_report(status="degraded"),
            provenance_fixture(),
            "fixture-build",
        )


def test_post_import_rejects_provenance_repository_mismatch(tmp_path: Path) -> None:
    provenance = provenance_fixture()
    provenance["repositories"][0]["inventory_sha256"] = "b" * 64

    with pytest.raises(SourceScopeError, match="scope_provenance_mismatch"):
        validate_post_import(
            plan_fixture(),
            graph_database(tmp_path),
            capability_report(),
            provenance,
            "fixture-build",
        )


def test_aosp_post_import_retains_local_service_gate(tmp_path: Path) -> None:
    plan = plan_fixture()
    plan["analysis_scope"] = "aosp"

    with pytest.raises(
        SourceScopeError,
        match="aosp_representative_evidence_missing",
    ):
        validate_post_import(
            plan,
            graph_database(tmp_path),
            capability_report(),
            provenance_fixture(),
            "fixture-build",
        )


def test_scope_report_is_deterministic_and_written_atomically(tmp_path: Path) -> None:
    first = validate_preflight(plan_fixture())
    second_plan = plan_fixture()
    second_plan["repositories"] = list(reversed(second_plan["repositories"]))
    second = validate_preflight(second_plan)

    assert scope_report_fingerprint(first) == scope_report_fingerprint(second)

    output = tmp_path / "scope.json"
    written = write_scope_report(output, first)
    loaded = json.loads(output.read_text(encoding="utf-8"))

    assert written == loaded
    assert loaded["fingerprint"] == scope_report_fingerprint(loaded)


def test_cli_runs_preflight_and_post_import(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    capability_path = tmp_path / "capability.json"
    provenance_path = tmp_path / "provenance.json"
    report_path = tmp_path / "scope.json"
    database = graph_database(tmp_path)
    plan_path.write_text(json.dumps(plan_fixture()), encoding="utf-8")
    capability_path.write_text(json.dumps(capability_report()), encoding="utf-8")
    provenance_path.write_text(json.dumps(provenance_fixture()), encoding="utf-8")

    assert main([
        "preflight", "--plan", str(plan_path), "--output", str(report_path)
    ]) == 0
    assert json.loads(report_path.read_text())["status"] == "preflight_passed"

    assert main([
        "post-import",
        "--plan", str(plan_path),
        "--db", str(database),
        "--capability-report", str(capability_path),
        "--provenance", str(provenance_path),
        "--build-id", "fixture-build",
        "--output", str(report_path),
    ]) == 0
    assert json.loads(report_path.read_text())["status"] == "passed"


def test_publication_fingerprint_binds_scope_without_changing_graph_fact(
    tmp_path: Path,
) -> None:
    fingerprints = tmp_path / "semantic-fingerprints.json"
    fingerprints.write_text(
        json.dumps({"whole_graph": "a" * 64}),
        encoding="utf-8",
    )
    scope_path = tmp_path / "scope.json"
    scope = write_scope_report(scope_path, validate_preflight(plan_fixture()))

    payload = bind_publication_scope_fingerprint(fingerprints, scope_path)

    assert payload["whole_graph"] == "a" * 64
    assert payload["publication_scope"] == scope_report_fingerprint(scope)


def test_scope_semantic_fingerprint_ignores_build_identity() -> None:
    first = validate_preflight(plan_fixture())
    first["build_id"] = "build-one"
    second = deepcopy(first)
    second["build_id"] = "build-two"

    assert scope_report_fingerprint(first) == scope_report_fingerprint(second)
