from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from collectors.permission.materializer import materialize_permission_facts
from collectors.permission.model import (
    PermissionEvidence,
    PermissionFact,
    PermissionFactKind,
)
from graph.writer import GraphWriter, Node


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def evidence(path: str, dialect: str = "manifest") -> PermissionEvidence:
    return PermissionEvidence(
        repository="frameworks/base",
        source_path=path,
        source_dialect=dialect,
        source_expression="android.permission.CAMERA",
        line_start=4,
        line_end=4,
        source_revision="a" * 40,
        parser="test_parser",
    )


def fact(
    kind: PermissionFactKind,
    *,
    package: str | None = None,
    owner: str | None = None,
    path: str = "frameworks/base/AndroidManifest.xml",
) -> PermissionFact:
    return PermissionFact(
        kind=kind,
        permission_name="android.permission.CAMERA",
        package_name=package,
        owner_node_id=owner,
        properties={"marker": kind.value},
        evidence=evidence(path, "java" if owner else "manifest"),
    )


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )


def test_materializer_writes_all_exact_directions_and_evidence(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    method = "JAVA_METHOD:example.Service#open()"
    writer = GraphWriter(database)
    writer.upsert_node(
        Node(
            node_id=method,
            node_type="JAVA_METHOD",
            display_name="open",
            source_path="frameworks/base/Service.java",
            line_start=1,
            line_end=20,
        )
    )
    facts = (
        fact(PermissionFactKind.DECLARES_PERMISSION),
        fact(PermissionFactKind.REQUESTS_PERMISSION, package="com.example"),
        fact(PermissionFactKind.ALLOWLISTS_PRIVILEGED_PERMISSION, package="com.example"),
        fact(PermissionFactKind.DENIES_PRIVILEGED_PERMISSION, package="com.example"),
        fact(PermissionFactKind.DEFAULT_GRANTS_PERMISSION, package="com.example"),
        fact(PermissionFactKind.REQUIRES_PERMISSION, owner=method, path="frameworks/base/Service.java"),
        fact(PermissionFactKind.CHECKS_PERMISSION, owner=method, path="frameworks/base/Service.java"),
        fact(PermissionFactKind.ENFORCES_PERMISSION, owner=method, path="frameworks/base/Service.java"),
    )

    summary = materialize_permission_facts(writer, reversed(facts))
    writer.close()

    assert summary.edge_counts == {kind.value: 1 for kind in PermissionFactKind}
    assert summary.diagnostics == ()
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT e.edge_type, source.node_type, target.node_type,
                   e.properties_json, e.source_revision
            FROM edge e
            JOIN node source ON source.node_id=e.from_node_id
            JOIN node target ON target.node_id=e.to_node_id
            WHERE e.edge_type LIKE '%PERMISSION%'
            ORDER BY e.edge_type
            """
        ).fetchall()
    endpoints = {row[0]: (row[1], row[2]) for row in rows}
    assert endpoints == {
        "ALLOWLISTS_PRIVILEGED_PERMISSION": ("ANDROID_PACKAGE", "PERMISSION"),
        "CHECKS_PERMISSION": ("JAVA_METHOD", "PERMISSION"),
        "DECLARES_PERMISSION": ("FILE", "PERMISSION"),
        "DEFAULT_GRANTS_PERMISSION": ("ANDROID_PACKAGE", "PERMISSION"),
        "DENIES_PRIVILEGED_PERMISSION": ("ANDROID_PACKAGE", "PERMISSION"),
        "ENFORCES_PERMISSION": ("JAVA_METHOD", "PERMISSION"),
        "REQUESTS_PERMISSION": ("ANDROID_PACKAGE", "PERMISSION"),
        "REQUIRES_PERMISSION": ("JAVA_METHOD", "PERMISSION"),
    }
    for _edge_type, _source, _target, raw_properties, revision in rows:
        properties = json.loads(raw_properties)
        assert properties["fact_identity"]
        assert properties["repository"] == "frameworks/base"
        assert properties["marker"]
        assert revision == "a" * 40


def test_missing_method_owner_becomes_diagnostic_not_dangling_edge(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    writer = GraphWriter(database)

    summary = materialize_permission_facts(
        writer,
        (fact(PermissionFactKind.CHECKS_PERMISSION, owner="JAVA_METHOD:missing"),),
    )
    writer.close()

    assert summary.edge_counts == {}
    assert [item.reason_code for item in summary.diagnostics] == [
        "missing_materialization_owner"
    ]
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM edge").fetchone() == (0,)
