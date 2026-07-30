from __future__ import annotations

import sqlite3
from pathlib import Path

from graph.writer import Edge, GraphWriter, Node


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )


def test_evidence_distinct_edges_have_distinct_identities() -> None:
    first = Edge(
        edge_type="REQUESTS_PERMISSION",
        from_node_id="ANDROID_PACKAGE:com.example",
        to_node_id="PERMISSION:android.permission.CAMERA",
        source_path="frameworks/base/AndroidManifest.xml",
        line_start=10,
        line_end=12,
        properties={"fact_identity": "first"},
    )
    second = Edge(
        edge_type=first.edge_type,
        from_node_id=first.from_node_id,
        to_node_id=first.to_node_id,
        source_path=first.source_path,
        line_start=first.line_start,
        line_end=first.line_end,
        properties={"fact_identity": "second"},
    )

    assert first.edge_id != second.edge_id


def test_fact_source_revision_overrides_writer_fallback(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    writer = GraphWriter(database, source_revision="fallback")
    writer.upsert_node(
        Node(
            node_id="ANDROID_PACKAGE:com.example",
            node_type="ANDROID_PACKAGE",
            display_name="com.example",
            source_revision="node-revision",
        )
    )
    writer.upsert_node(
        Node(
            node_id="PERMISSION:android.permission.CAMERA",
            node_type="PERMISSION",
            display_name="android.permission.CAMERA",
        )
    )
    writer.upsert_edge(
        Edge(
            edge_type="REQUESTS_PERMISSION",
            from_node_id="ANDROID_PACKAGE:com.example",
            to_node_id="PERMISSION:android.permission.CAMERA",
            source_revision="edge-revision",
        )
    )
    writer.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT source_revision FROM node WHERE node_id=?",
            ("ANDROID_PACKAGE:com.example",),
        ).fetchone() == ("node-revision",)
        assert connection.execute(
            "SELECT source_revision FROM node WHERE node_id=?",
            ("PERMISSION:android.permission.CAMERA",),
        ).fetchone() == ("fallback",)
        assert connection.execute(
            "SELECT source_revision FROM edge WHERE edge_type=?",
            ("REQUESTS_PERMISSION",),
        ).fetchone() == ("edge-revision",)


def test_repository_scoped_definitions_preserve_duplicate_logical_symbol(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    create_database(database)
    writer = GraphWriter(database)
    logical = Node(
        node_id="JAVA_CLASS:common.Duplicate",
        node_type="JAVA_CLASS",
        qualified_name="common.Duplicate",
        display_name="Duplicate",
    )
    writer.upsert_node(logical)
    first = writer.upsert_symbol_definition(
        logical,
        repository="frameworks/base",
        source_path="frameworks/base/Duplicate.java",
        line_start=1,
        line_end=3,
    )
    second = writer.upsert_symbol_definition(
        logical,
        repository="vendor/demo",
        source_path="vendor/demo/Duplicate.java",
        line_start=1,
        line_end=3,
    )
    writer.close()

    assert first != second
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM node WHERE node_type='SYMBOL_DEFINITION'"
        ).fetchone() == (2,)
        properties = connection.execute(
            "SELECT properties_json FROM node WHERE node_id=?",
            (logical.node_id,),
        ).fetchone()[0]
        assert '"definition_resolution": "ambiguous"' in properties
        assert '"definition_count": 2' in properties
        assert connection.execute(
            """
            SELECT COUNT(*) FROM edge
            WHERE edge_type='DEFINES_SYMBOL'
              AND to_node_id='JAVA_CLASS:common.Duplicate'
            """
        ).fetchone() == (2,)
