from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from collectors.codeql.corrections import apply_corrections, parse_correction
from workspace.call_dataflow_validation import (
    CallDataflowValidationError,
    validate_call_dataflow,
)
from workspace.schema_migrations import apply_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def database(tmp_path: Path) -> Path:
    path = tmp_path / "graph.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )
    apply_migrations(path, PROJECT_ROOT / "storage/migrations")
    return path


def insert_node(connection: sqlite3.Connection, node_id: str) -> None:
    connection.execute(
        "INSERT INTO node VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            node_id,
            "JAVA_METHOD",
            node_id,
            node_id,
            "{}",
            "demo/A.java",
            1,
            1,
            "abc",
            "fixture",
            "1",
            node_id + "-hash",
            "active",
            "2026-08-20T00:00:00+00:00",
        ),
    )


def insert_run(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO extraction_run VALUES(
          'run-1','call_graph',?,?,'aosp','userdebug','["services"]',
          '2.26.3','java-kotlin',?,10,10,'complete',?,?,'{}'
        )
        """,
        ("d" * 64, "s" * 64, "l" * 64, "2026-08-20T00:00:00Z", "2026-08-20T01:00:00Z"),
    )
    connection.execute(
        "INSERT INTO extraction_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "evidence-1",
            "run-1",
            "android-context/codeql",
            "CallSites",
            "1",
            "r" * 64,
            "raw/results.csv",
            "d" * 64,
            "e" * 64,
            "{}",
        ),
    )


def test_validator_rejects_accepted_target_with_ambiguous_endpoint(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        insert_run(connection)
        for node_id in ("caller", "callee", "call-site"):
            insert_node(connection, node_id)
        connection.execute(
            "INSERT INTO semantic_definition VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "caller-definition", "run-1", "caller", "demo.A#caller()", "java",
                "method", "demo", "demo/A.java", 1, 1, 1, 10, "unique",
                "caller-definition-hash", "{}",
            ),
        )
        insert_node(connection, "caller-definition")
        connection.execute(
            "INSERT INTO semantic_definition VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "callee-definition", "run-1", None, "demo.A#callee()", "java",
                "method", "demo", "demo/A.java", 2, 1, 2, 10, "ambiguous",
                "callee-definition-hash", "{}",
            ),
        )
        insert_node(connection, "callee-definition")
        connection.execute(
            "INSERT INTO call_site VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "call-site", "run-1", "caller", "demo", "demo/A.java", 3, 1,
                3, 8, "x" * 64, "virtual", "resolved", 1, "c" * 64, "{}",
            ),
        )
        connection.execute(
            "INSERT INTO call_target VALUES(?,?,?,?,?)",
            ("call-site", "callee", "must", "evidence-1", "t" * 64),
        )

    with pytest.raises(CallDataflowValidationError, match="ambiguous endpoint"):
        validate_call_dataflow(path, require_aosp_evidence=False)


def test_validator_requires_contiguous_path_steps(tmp_path: Path) -> None:
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        insert_run(connection)
        for node_id in ("owner", "value-0", "value-1", "path-1"):
            insert_node(connection, node_id)
        for value_id, line in (("value-0", 1), ("value-1", 3)):
            connection.execute(
                "INSERT INTO program_value VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value_id, "run-1", "owner", "expression", None, None, "demo",
                    "demo/A.java", line, 1, line, 4, value_id + "-expr",
                    value_id + "-content", "{}",
                ),
            )
        connection.execute(
            "INSERT INTO dataflow_path VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "path-1", "run-1", "permission", "value-0", "value-1",
                "interprocedural", "exact", 2, "p" * 64, "evidence-1", "accepted",
                "demo", "demo/A.java", "{}",
            ),
        )
        for ordinal, value_id, line in ((0, "value-0", 1), (2, "value-1", 3)):
            connection.execute(
                "INSERT INTO dataflow_step VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "path-1", ordinal, value_id, "flow", "demo", "demo/A.java",
                    line, 1, line, 4, "step", f"step-{ordinal}",
                ),
            )

    with pytest.raises(CallDataflowValidationError, match="non-contiguous"):
        validate_call_dataflow(path, require_aosp_evidence=False)


def test_validator_reports_reconciliation_quality(tmp_path: Path) -> None:
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        insert_run(connection)
        for index, status in enumerate(("unique", "unique", "unmatched")):
            definition_id = f"definition-{index}"
            logical_id = f"logical-{index}" if status == "unique" else None
            insert_node(connection, definition_id)
            if logical_id:
                insert_node(connection, logical_id)
            connection.execute(
                "INSERT INTO semantic_definition VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    definition_id, "run-1", logical_id, f"demo.A#m{index}()", "java",
                    "method", "demo", "demo/A.java", index + 1, 1, index + 1, 5,
                    status, f"definition-{index}-hash", "{}",
                ),
            )

    report = validate_call_dataflow(path, require_aosp_evidence=False)

    assert report.metrics["eligible_definitions"] == 3
    assert report.metrics["unique_definitions"] == 2
    assert report.metrics["reconciliation_percent"] == pytest.approx(66.666, rel=0.01)


def test_stale_correction_is_reported_non_strict_and_rejected_strict(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        insert_run(connection)
        for node_id in ("a", "b"):
            insert_node(connection, node_id)
        connection.execute(
            "INSERT INTO edge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "edge-1", "CALLS", "a", "b", "{}", "demo/A.java", 1, 1,
                "abc", "fixture", "1", "a" * 64, "active", "2026-08-20T00:00:00Z",
            ),
        )
    correction = parse_correction(
        {
            "correction_id": "CORR-STALE",
            "action": "suppress",
            "target_fact_uri": "edge:edge-1",
            "expected_content_hash": "0" * 64,
            "applicable_source_revision": "abc",
            "reason": "reviewed",
            "evidence_refs": ["review:1"],
            "author": "a",
            "approved_by": "b",
            "approval_ref": "CR-1",
        }
    )
    apply_corrections(path, (correction,), source_revision="abc", run_id="run-1")

    report = validate_call_dataflow(path, require_aosp_evidence=False)

    assert "stale corrections: 1" in report.warnings
    with pytest.raises(CallDataflowValidationError, match="non-active corrections"):
        validate_call_dataflow(path, require_aosp_evidence=True)


def insert_definition(
    connection: sqlite3.Connection,
    *,
    definition_id: str,
    logical_id: str,
    symbol_key: str,
    line: int,
) -> None:
    insert_node(connection, logical_id)
    insert_node(connection, definition_id)
    connection.execute(
        "INSERT INTO semantic_definition VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            definition_id, "run-1", logical_id, symbol_key, "java", "method",
            "frameworks/base", "frameworks/base/demo/A.java", line, 1, line, 20,
            "unique", definition_id + "-content", "{}",
        ),
    )


def write_evidence_config(path: Path) -> None:
    path.write_text(
        """
[acceptance]
minimum_identity_reconciliation_percent = 95

[[acceptance.strong_evidence]]
id = "exact-call"
kind = "call"
caller_symbol_key = "java|method|demo.A#caller()"
callee_symbol_key = "java|method|demo.B#callee()"
relation_kind = "must"
source_path = "frameworks/base/demo/A.java"

[[acceptance.strong_evidence]]
id = "forbidden-call"
kind = "absent_call"
caller_symbol_key = "java|method|demo.A#caller()"
callee_symbol_key = "java|method|demo.C#wrong()"
relation_kind = "must"
source_path = "frameworks/base/demo/A.java"

[[acceptance.strong_evidence]]
id = "guarded-security-trace"
kind = "security_trace"
entry_symbol_key = "java|method|demo.A#caller()"
scenario_id = "demo-sensitive-flow"
status = "guarded"
source_path = "frameworks/base/demo/A.java"
minimum_guard_count = 1
minimum_identity_transition_count = 0
required_step_kinds = ["guard"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_strong_evidence_requires_exact_call_and_committed_negative(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    config = tmp_path / "codeql.toml"
    write_evidence_config(config)
    with sqlite3.connect(path) as connection:
        insert_run(connection)
        insert_definition(
            connection,
            definition_id="caller-definition",
            logical_id="caller-logical",
            symbol_key="java|method|demo.A#caller()",
            line=1,
        )
        insert_definition(
            connection,
            definition_id="callee-definition",
            logical_id="callee-logical",
            symbol_key="java|method|demo.B#callee()",
            line=2,
        )
        insert_node(connection, "call-exact")
        connection.execute(
            "INSERT INTO call_site VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "call-exact", "run-1", "caller-logical", "frameworks/base",
                "frameworks/base/demo/A.java", 3, 1, 3, 10, "x" * 64,
                "static", "resolved", 1, "c" * 64, "{}",
            ),
        )
        connection.execute(
            "INSERT INTO call_target VALUES(?,?,?,?,?)",
            ("call-exact", "callee-logical", "must", "evidence-1", "t" * 64),
        )
        insert_node(connection, "trace-exact")
        connection.execute(
            "INSERT INTO security_trace VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "trace-exact", "run-1", "demo-sensitive-flow", "caller-logical",
                "call-exact", 1, 0, "trace-fingerprint", "guarded",
                "frameworks/base", "frameworks/base/demo/A.java", "{}",
            ),
        )
        connection.execute(
            "INSERT INTO security_trace_step VALUES(?,?,?,?,?,?,?,?)",
            (
                "trace-exact", 0, "guard", None, None, "call-exact", None,
                "trace-step-content",
            ),
        )

    report = validate_call_dataflow(
        path, require_aosp_evidence=True, config_path=config
    )

    assert report.strong_evidence == {
        "exact-call": "pass",
        "forbidden-call": "pass",
        "guarded-security-trace": "pass",
    }


def test_strong_evidence_does_not_accept_representative_class_only(
    tmp_path: Path,
) -> None:
    path = database(tmp_path)
    config = tmp_path / "codeql.toml"
    write_evidence_config(config)
    with sqlite3.connect(path) as connection:
        insert_node(connection, "demo.A")

    with pytest.raises(CallDataflowValidationError, match="exact-call"):
        validate_call_dataflow(path, require_aosp_evidence=True, config_path=config)
