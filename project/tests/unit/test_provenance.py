from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace.provenance import (
    ProvenanceError,
    collect_provenance,
    main,
    provenance_fingerprint,
    validate_provenance,
)
from workspace.source_scope_validation import write_scope_report


def complete_provenance() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "repositories": [
            {
                "name": "frameworks/base",
                "path": "frameworks/base",
                "state": "dirty",
                "revision": "a" * 40,
                "dirty": True,
                "inventory_sha256": "b" * 64,
                "file_count": 10,
            }
        ],
        "configs": {
            "source_roots.default.toml": {"sha256": "c" * 64},
            "source_roots.local.toml": {"sha256": None},
            "parser_registry.toml": {"sha256": "d" * 64},
        },
        "tools": {
            "python": {"status": "available", "version": "3.12"},
            "sqlite": {"status": "available", "version": "3.45"},
            "ctags": {"status": "available", "version": "6.1"},
            "jadx": {"status": "optional_missing", "version": None},
        },
    }


def test_dirty_repository_is_valid_and_changes_fingerprint() -> None:
    first = complete_provenance()
    second = json.loads(json.dumps(first))
    second["repositories"][0]["dirty"] = False
    second["repositories"][0]["state"] = "clean"

    validate_provenance(first, require_complete=True)
    validate_provenance(second, require_complete=True)

    assert provenance_fingerprint(first) != provenance_fingerprint(second)


def test_requested_strict_gate_rejects_missing_inventory() -> None:
    payload = complete_provenance()
    payload["repositories"][0]["inventory_sha256"] = None

    with pytest.raises(ProvenanceError, match="missing provenance"):
        validate_provenance(payload, require_complete=True)


def test_requested_strict_gate_rejects_tampered_fingerprint() -> None:
    payload = complete_provenance()
    payload["fingerprint"] = "0" * 64

    with pytest.raises(ProvenanceError, match="fingerprint mismatch"):
        validate_provenance(payload, require_complete=True)


def test_collect_records_repository_config_and_tool_identities(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "aosp/frameworks/base"
    repository.mkdir(parents=True)
    (repository / "Source.java").write_text("class Source {}\n", encoding="utf-8")
    source = tmp_path / "source_roots.default.toml"
    source.write_text("[workspace]\n", encoding="utf-8")
    registry = tmp_path / "parser_registry.toml"
    registry.write_text("[parsers]\n", encoding="utf-8")
    plan = tmp_path / "execution-plan.json"
    inventory = __import__(
        "workspace.revisions", fromlist=["inspect_repository_provenance"]
    ).inspect_repository_provenance(repository)
    plan.write_text(
        json.dumps(
            {
                "aosp_root": str(tmp_path / "aosp"),
                "default_exclude": [],
                "repositories": [
                    {
                        "name": "frameworks/base",
                        "path": "frameworks/base",
                        "enabled": True,
                        "include": [],
                        "exclude": [],
                        "languages": ["java"],
                        "revision": inventory.revision,
                        "inventory_sha256": inventory.inventory_sha256,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = collect_provenance(plan, source, registry)

    assert payload["repositories"][0]["state"] == "non_git"
    assert payload["repositories"][0]["matches_plan"] is True
    assert payload["configs"]["source_roots.default.toml"]["sha256"]
    assert payload["configs"]["parser_registry.toml"]["sha256"]
    assert payload["tools"]["python"]["status"] == "available"
    assert payload["tools"]["sqlite"]["status"] == "available"
    assert payload["tools"]["ctags"]["status"] in {"available", "missing"}
    assert payload["tools"]["jadx"]["status"] in {
        "available",
        "optional_missing",
        "failed",
    }
    assert payload["fingerprint"]
    assert provenance_fingerprint(payload) == payload["fingerprint"]

    output = tmp_path / "provenance.json"
    assert main(
        [
            "collect",
            "--plan",
            str(plan),
            "--source-config",
            str(source),
            "--registry",
            str(registry),
            "--output",
            str(output),
        ]
    ) == 0
    assert output.is_file()


def test_collect_includes_codeql_corrections_and_semantic_fingerprints(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "aosp/frameworks/base"
    repository.mkdir(parents=True)
    (repository / "Source.java").write_text("class Source {}\n", encoding="utf-8")
    source = tmp_path / "source.toml"
    source.write_text("[workspace]\n", encoding="utf-8")
    registry = tmp_path / "registry.toml"
    registry.write_text("[parsers]\n", encoding="utf-8")
    inventory = __import__(
        "workspace.revisions", fromlist=["inspect_repository_provenance"]
    ).inspect_repository_provenance(repository)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "aosp_root": str(tmp_path / "aosp"),
                "default_exclude": [],
                "repositories": [
                    {
                        "name": "frameworks/base",
                        "path": "frameworks/base",
                        "enabled": True,
                        "include": [],
                        "exclude": [],
                        "languages": ["java"],
                        "revision": inventory.revision,
                        "inventory_sha256": inventory.inventory_sha256,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    codeql = tmp_path / "codeql.json"
    codeql.write_text(json.dumps({"status": "complete", "run_id": "run-1"}), encoding="utf-8")
    corrections = tmp_path / "corrections.json"
    corrections.write_text(json.dumps({"applications": []}), encoding="utf-8")
    fingerprints = tmp_path / "fingerprints.json"
    fingerprints.write_text(json.dumps({"whole_graph": "a" * 64}), encoding="utf-8")

    payload = collect_provenance(
        plan,
        source,
        registry,
        codeql_report=codeql,
        correction_report=corrections,
        fingerprints=fingerprints,
    )

    semantic = payload["semantic_pipeline"]
    assert semantic["codeql_report"]["sha256"]
    assert semantic["correction_report"]["sha256"]
    assert semantic["fingerprints"]["sha256"]


def test_collect_binds_verified_source_scope_report(tmp_path: Path) -> None:
    repository = tmp_path / "aosp/frameworks/base"
    repository.mkdir(parents=True)
    (repository / "Source.java").write_text("class Source {}\n", encoding="utf-8")
    inventory = __import__(
        "workspace.revisions", fromlist=["inspect_repository_provenance"]
    ).inspect_repository_provenance(repository)
    source = tmp_path / "source.toml"
    source.write_text("[workspace]\n", encoding="utf-8")
    registry = tmp_path / "registry.toml"
    registry.write_text("[parsers]\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "aosp_root": str(tmp_path / "aosp"),
                "analysis_scope": "partial",
                "repositories": [
                    {
                        "name": "frameworks/base",
                        "path": "frameworks/base",
                        "enabled": True,
                        "include": [],
                        "exclude": [],
                        "languages": ["java"],
                        "revision": inventory.revision,
                        "inventory_sha256": inventory.inventory_sha256,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    scope_report = tmp_path / "source-scope-validation.json"
    scope_payload = write_scope_report(
        scope_report,
        {
            "schema_version": 1,
            "build_id": "build-1",
            "analysis_scope": "partial",
            "full_aosp_coverage": False,
            "status": "passed",
            "enabled_repositories": [],
            "scheduled_task_count": 1,
            "source_backed_node_count": 1,
            "capability_counts": {"supported": 1},
            "validation_errors": [],
        },
    )

    payload = collect_provenance(
        plan,
        source,
        registry,
        scope_report=scope_report,
    )

    assert payload["source_scope"]["payload"] == scope_payload
    assert payload["source_scope"]["sha256"]
    validate_provenance(payload, require_complete=True)


def test_provenance_rejects_tampered_embedded_scope_payload(tmp_path: Path) -> None:
    payload = complete_provenance()
    scope_path = tmp_path / "scope.json"
    scope = write_scope_report(
        scope_path,
        {
            "schema_version": 1,
            "build_id": "build-1",
            "analysis_scope": "partial",
            "full_aosp_coverage": False,
            "status": "passed",
            "enabled_repositories": [],
            "scheduled_task_count": 1,
            "source_backed_node_count": 1,
            "capability_counts": {"supported": 1},
            "validation_errors": [],
        },
    )
    payload["source_scope"] = {
        "path": str(scope_path),
        "sha256": __import__("hashlib").sha256(scope_path.read_bytes()).hexdigest(),
        "payload": scope,
    }
    payload["source_scope"]["payload"]["analysis_scope"] = "aosp"

    with pytest.raises(ProvenanceError, match="source scope"):
        validate_provenance(payload, require_complete=True)
