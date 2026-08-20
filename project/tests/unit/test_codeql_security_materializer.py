from __future__ import annotations

import sqlite3
from pathlib import Path

from collectors.codeql.materializer import MaterializationRun, materialize_security_facts
from collectors.codeql.model import (
    DataflowPathRecord,
    GuardRecord,
    IdentityTransitionRecord,
    ProgramValueRecord,
    SourceSpan,
)
from workspace.schema_migrations import apply_migrations


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = "frameworks/base/demo/SecurityService.java"
OWNER_KEY = "demo.SecurityService#entry(java.lang.String)"
HELPER_KEY = "demo.SecurityService#write(java.lang.String)"


def graph_db(tmp_path: Path) -> Path:
    database = tmp_path / "graph.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8"))
    apply_migrations(database, PROJECT_ROOT / "storage/migrations")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO extraction_run VALUES(
              'run-1','security','dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
              'ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss',
              'aosp','userdebug','["services"]','2.26.3','java-kotlin',
              'llllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllll',
              1,1,'running','2026-08-20T00:00:00+00:00',NULL,'{}'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO extraction_evidence VALUES(
              'evidence-1','run-1','android-context/java-kotlin-call-dataflow',
              'SystemServiceDataflow','1',
              'rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr',
              '/tmp/result.bqrs',
              'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
              'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc','{}'
            )
            """
        )
        seed_node(connection, "JAVA_METHOD:entry", "JAVA_METHOD", OWNER_KEY, 10)
        seed_node(connection, "SEMANTIC_DEFINITION:entry", "SEMANTIC_DEFINITION", OWNER_KEY, 10)
        connection.execute(
            """
            INSERT INTO semantic_definition VALUES(
              'SEMANTIC_DEFINITION:entry','run-1','JAVA_METHOD:entry',?,'java','method',
              'frameworks/base',?,10,1,40,1,'unique',?,'{}'
            )
            """,
            (OWNER_KEY, SOURCE, "h" * 64),
        )
        for call_id, line in (
            ("CALL_SITE:guard", 14),
            ("CALL_SITE:sink", 15),
            ("CALL_SITE:clear", 20),
            ("CALL_SITE:restore", 24),
        ):
            seed_node(connection, call_id, "CALL_SITE", call_id, line)
            connection.execute(
                """
                INSERT INTO call_site VALUES(
                  ?,'run-1','JAVA_METHOD:entry','frameworks/base',?,?,?,?,?,
                  ?, 'static','resolved',1,?,'{}'
                )
                """,
                (call_id, SOURCE, line, 1, line, 8, "e" * 64, "c" * 64),
            )
    return database


def seed_node(
    connection: sqlite3.Connection,
    node_id: str,
    node_type: str,
    qualified_name: str,
    line: int,
) -> None:
    connection.execute(
        "INSERT INTO node VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            node_id, node_type, qualified_name, qualified_name, "{}", SOURCE,
            line, line, "abc", "fixture", "1", "h" * 64, "active",
            "2026-08-20T00:00:00+00:00",
        ),
    )


def span(line: int) -> SourceSpan:
    return SourceSpan("frameworks/base", SOURCE, line, 1, line, 8)


def path_record() -> DataflowPathRecord:
    steps = (
        ProgramValueRecord("value", OWNER_KEY, "parameter", 0, span(10), parameter_index=0),
        ProgramValueRecord("argument", OWNER_KEY, "expression", 1, span(11)),
        ProgramValueRecord("return", OWNER_KEY, "return", 2, span(12)),
        ProgramValueRecord("sink", OWNER_KEY, "expression", 3, span(15)),
    )
    return DataflowPathRecord(
        "binder_argument_to_sensitive_sink", OWNER_KEY, OWNER_KEY,
        steps, "SystemServiceDataflow", "1", "d" * 64,
    )


def run_context() -> MaterializationRun:
    return MaterializationRun("run-1", "evidence-1", "abc")


def edge_count(database: Path, edge_type: str) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM edge WHERE edge_type=?", (edge_type,)
        ).fetchone()[0])


def test_path_preserves_program_values_and_ordered_steps(tmp_path: Path) -> None:
    database = graph_db(tmp_path)

    materialize_security_facts(database, (path_record(),), run_context())

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT ordinal,step_kind FROM dataflow_step ORDER BY ordinal"
        ).fetchall()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert rows == [(0, "source"), (1, "argument"), (2, "return"), (3, "sink")]
    assert edge_count(database, "FLOW_SOURCE") == 1
    assert edge_count(database, "FLOW_SINK") == 1
    assert violations == []


def test_interprocedural_path_uses_sink_owner_for_security_trace(
    tmp_path: Path,
) -> None:
    database = graph_db(tmp_path)
    with sqlite3.connect(database) as connection:
        seed_node(connection, "JAVA_METHOD:helper", "JAVA_METHOD", HELPER_KEY, 30)
        seed_node(
            connection,
            "SEMANTIC_DEFINITION:helper",
            "SEMANTIC_DEFINITION",
            HELPER_KEY,
            30,
        )
        connection.execute(
            """
            INSERT INTO semantic_definition VALUES(
              'SEMANTIC_DEFINITION:helper','run-1','JAVA_METHOD:helper',?,'java','method',
              'frameworks/base',?,30,1,40,1,'unique',?,'{}'
            )
            """,
            (HELPER_KEY, SOURCE, "i" * 64),
        )
        connection.execute(
            "UPDATE call_site SET caller_method_id='JAVA_METHOD:helper' "
            "WHERE call_site_id='CALL_SITE:sink'"
        )
    path = DataflowPathRecord(
        "binder_argument_to_sensitive_sink",
        OWNER_KEY,
        HELPER_KEY,
        (
            ProgramValueRecord(
                "value", OWNER_KEY, "parameter", 0, span(10), parameter_index=0
            ),
            ProgramValueRecord("sink", HELPER_KEY, "expression", 1, span(15)),
        ),
        "SystemServiceDataflow",
        "1",
        "d" * 64,
    )

    report = materialize_security_facts(database, (path,), run_context())

    assert report.security_traces == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT sink_call_site_id FROM security_trace"
        ).fetchone()[0] == "CALL_SITE:sink"


def test_guard_is_not_materialized_as_dataflow(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    guard = GuardRecord(
        OWNER_KEY, "android.content.Context.enforceCallingPermission",
        "demo.Store.writeSecureSetting", "dominates", 14, 15, SOURCE,
        "SystemServiceGuards", "1", "d" * 64,
    )

    materialize_security_facts(database, (path_record(), guard), run_context())

    assert edge_count(database, "GUARDED_BY") == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM dataflow_step WHERE step_kind='guard'"
        ).fetchone()[0] == 0


def test_unpaired_identity_restore_remains_diagnostic(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    identity = IdentityTransitionRecord(
        OWNER_KEY, 20, None, "missing_all_exit_restore", SOURCE,
        "BinderIdentity", "1", "d" * 64,
    )

    materialize_security_facts(database, (path_record(), identity), run_context())

    assert edge_count(database, "IDENTITY_CLEARED_BY") == 1
    assert edge_count(database, "IDENTITY_RESTORED_BY") == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM security_trace").fetchone()[0] == "identity_unpaired"


def test_paired_identity_requires_restore_and_replay_clears_stale_safe_edge(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    paired = IdentityTransitionRecord(
        OWNER_KEY, 20, 24, "paired_all_exits", SOURCE,
        "BinderIdentity", "1", "d" * 64,
    )
    unpaired = IdentityTransitionRecord(
        OWNER_KEY, 20, None, "missing_all_exit_restore", SOURCE,
        "BinderIdentity", "1", "d" * 64,
    )

    materialize_security_facts(database, (path_record(), paired), run_context())
    assert edge_count(database, "IDENTITY_RESTORED_BY") == 1
    materialize_security_facts(database, (path_record(), unpaired), run_context())

    assert edge_count(database, "IDENTITY_CLEARED_BY") == 1
    assert edge_count(database, "IDENTITY_RESTORED_BY") == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM security_trace").fetchone()[0] == "identity_unpaired"
