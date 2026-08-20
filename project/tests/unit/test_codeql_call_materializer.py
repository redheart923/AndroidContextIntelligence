from __future__ import annotations

import sqlite3
from pathlib import Path

from collectors.codeql.materializer import MaterializationRun, materialize_call_graph
from collectors.codeql.model import (
    CallSiteRecord,
    CallTargetRecord,
    DefinitionRecord,
    SourceSpan,
)
from workspace.call_dataflow_validation import validate_call_dataflow
from workspace.schema_migrations import apply_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def graph_db(tmp_path: Path) -> Path:
    database = tmp_path / "graph.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8"))
    apply_migrations(database, PROJECT_ROOT / "storage/migrations")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO extraction_run(
              run_id,capability,database_fingerprint,source_fingerprint,product,
              variant,build_targets_json,codeql_version,extractor_version,
              query_pack_lock_hash,observed_file_count,observed_method_count,
              status,started_at,completed_at,properties_json
            ) VALUES('run-1','call_graph',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "d" * 64, "s" * 64, "aosp", "userdebug", '["services"]',
                "2.26.3", "java-kotlin", "l" * 64, 2, 2, "running",
                "2026-08-20T00:00:00+00:00", None, "{}",
            ),
        )
        connection.execute(
            """
            INSERT INTO extraction_evidence VALUES(
              'evidence-1','run-1','android-context/java-kotlin-call-dataflow',
              'CallSites','1',?,?,?,?,'{}'
            )
            """,
            ("r" * 64, "/tmp/CallSites.bqrs", "d" * 64, "c" * 64),
        )
    return database


def seed_method(
    database: Path,
    node_id: str,
    qualified_name: str,
    *,
    line: int,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO node VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                node_id, "JAVA_METHOD", qualified_name, qualified_name, "{}",
                "frameworks/base/demo/Service.java", line, line + 1, "abc",
                "ctags", "1", "h" * 64, "active", "2026-08-20T00:00:00+00:00",
            ),
        )


def definition(
    owner: str,
    name: str,
    parameters: tuple[str, ...],
    line: int,
) -> DefinitionRecord:
    symbol = f"{owner}#{name}({','.join(parameters)})"
    return DefinitionRecord(
        language="java", package_name="demo", declaring_type=owner,
        callable_kind="method", callable_name=name, erased_parameters=parameters,
        return_type="void", symbol_key=symbol,
        span=SourceSpan(
            "frameworks/base", "frameworks/base/demo/Service.java", line, 1, line + 1, 1
        ),
        query_id="CallSites", query_version="1", database_fingerprint="d" * 64,
    )


def call(
    caller: DefinitionRecord,
    targets: tuple[DefinitionRecord, ...],
    *,
    relation: str,
) -> CallSiteRecord:
    return CallSiteRecord(
        language="java", caller_symbol_key=caller.symbol_key,
        dispatch_kind="static" if relation == "must" else "virtual",
        relation_kind=relation, candidate_count=len(targets),
        expression_text="target(value)",
        unresolved_reason="missing_dependency" if relation == "unresolved" else "",
        span=SourceSpan(
            "frameworks/base", "frameworks/base/demo/Service.java", 15, 5, 15, 18
        ),
        targets=tuple(CallTargetRecord(target.symbol_key, relation) for target in targets),
        query_id="CallSites", query_version="1", database_fingerprint="d" * 64,
    )


def scalar(database: Path, sql: str) -> object:
    with sqlite3.connect(database) as connection:
        return connection.execute(sql).fetchone()[0]


def edge_count(database: Path, edge_type: str) -> int:
    with sqlite3.connect(database) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM edge WHERE edge_type=?", (edge_type,)
            ).fetchone()[0]
        )


def run_context() -> MaterializationRun:
    return MaterializationRun("run-1", "evidence-1", "abc")


def test_unique_direct_call_creates_must_call_and_calls_projection(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    caller = definition("demo.Service", "entry", ("java.lang.String",), 10)
    callee = definition("demo.Target", "run", ("java.lang.String",), 30)
    seed_method(database, "JAVA_METHOD:caller", "demo.Service#entry(String value)", line=10)
    seed_method(database, "JAVA_METHOD:callee", "demo.Target#run(String value)", line=30)

    report = materialize_call_graph(database, (caller, callee, call(caller, (callee,), relation="must")), run_context())

    assert report.accepted_targets == 1
    assert scalar(database, "SELECT relation_kind FROM call_target") == "must"
    assert edge_count(database, "MUST_CALL") == 1
    assert edge_count(database, "CALLS") == 1
    assert edge_count(database, "IN_METHOD") == 1


def test_polymorphic_call_retains_all_may_targets(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    caller = definition("demo.Service", "entry", ("java.lang.String",), 10)
    first = definition("demo.First", "run", ("java.lang.String",), 30)
    second = definition("demo.Second", "run", ("java.lang.String",), 40)
    seed_method(database, "JAVA_METHOD:caller", "demo.Service#entry(String value)", line=10)
    seed_method(database, "JAVA_METHOD:first", "demo.First#run(String value)", line=30)
    seed_method(database, "JAVA_METHOD:second", "demo.Second#run(String value)", line=40)

    materialize_call_graph(
        database,
        (caller, first, second, call(caller, (first, second), relation="may")),
        run_context(),
    )

    with sqlite3.connect(database) as connection:
        relations = connection.execute(
            "SELECT relation_kind FROM call_target ORDER BY callee_method_id"
        ).fetchall()
    assert relations == [("may",), ("may",)]
    assert edge_count(database, "MAY_CALL") == 2
    assert edge_count(database, "CALLS") == 2
    report = validate_call_dataflow(database, require_aosp_evidence=False)
    assert report.metrics["accepted_targets"] == 2


def test_ambiguous_callee_is_diagnostic_not_accepted_edge(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    caller = definition("demo.Service", "entry", ("java.lang.String",), 10)
    callee = definition("demo.Target", "run", ("java.lang.String",), 30)
    seed_method(database, "JAVA_METHOD:caller", "demo.Service#entry(String value)", line=10)
    seed_method(database, "JAVA_METHOD:first", "demo.Target#run(String value)", line=30)
    seed_method(database, "JAVA_METHOD:second", "demo.Target#run(java.lang.String arg)", line=30)

    report = materialize_call_graph(
        database, (caller, callee, call(caller, (callee,), relation="must")), run_context()
    )

    assert report.ambiguous_sites == 1
    assert scalar(database, "SELECT resolution_status FROM call_site") == "ambiguous"
    assert scalar(database, "SELECT COUNT(*) FROM call_target") == 0
    assert edge_count(database, "MUST_CALL") == 0
    assert edge_count(database, "CALLS") == 0


def test_unresolved_call_site_is_retained_without_target(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    caller = definition("demo.Service", "entry", ("java.lang.String",), 10)
    seed_method(database, "JAVA_METHOD:caller", "demo.Service#entry(String value)", line=10)
    unresolved = call(caller, (), relation="unresolved")

    materialize_call_graph(database, (caller, unresolved), run_context())

    assert scalar(database, "SELECT resolution_status FROM call_site") == "unresolved"
    assert scalar(database, "SELECT COUNT(*) FROM call_target") == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_replay_from_resolved_to_ambiguous_removes_stale_targets(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    caller = definition("demo.Service", "entry", ("java.lang.String",), 10)
    callee = definition("demo.Target", "run", ("java.lang.String",), 30)
    seed_method(database, "JAVA_METHOD:caller", "demo.Service#entry(String value)", line=10)
    seed_method(database, "JAVA_METHOD:first", "demo.Target#run(String value)", line=30)
    records = (caller, callee, call(caller, (callee,), relation="must"))
    materialize_call_graph(database, records, run_context())
    assert edge_count(database, "MUST_CALL") == 1

    seed_method(database, "JAVA_METHOD:second", "demo.Target#run(java.lang.String arg)", line=30)
    materialize_call_graph(database, records, run_context())

    assert scalar(database, "SELECT resolution_status FROM call_site") == "ambiguous"
    assert scalar(database, "SELECT COUNT(*) FROM call_target") == 0
    assert edge_count(database, "MUST_CALL") == 0
    assert edge_count(database, "CALLS") == 0
