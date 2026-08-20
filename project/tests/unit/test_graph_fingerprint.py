from __future__ import annotations

import sqlite3
from pathlib import Path

from graph.writer import Edge, GraphWriter, Node
from scripts.graph_fingerprint import (
    graph_semantic_fingerprint,
    graph_semantic_fingerprints,
)
from workspace.schema_migrations import apply_migrations


def create_schema(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE node (
              node_id TEXT PRIMARY KEY, node_type TEXT NOT NULL,
              qualified_name TEXT, display_name TEXT NOT NULL,
              properties_json TEXT NOT NULL DEFAULT '{}', source_path TEXT,
              line_start INTEGER, line_end INTEGER, source_revision TEXT,
              extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
              content_hash TEXT, status TEXT NOT NULL DEFAULT 'active',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE edge (
              edge_id TEXT PRIMARY KEY, edge_type TEXT NOT NULL,
              from_node_id TEXT NOT NULL, to_node_id TEXT NOT NULL,
              properties_json TEXT NOT NULL DEFAULT '{}', source_path TEXT,
              line_start INTEGER, line_end INTEGER, source_revision TEXT,
              extractor TEXT NOT NULL, extractor_version TEXT NOT NULL,
              content_hash TEXT, status TEXT NOT NULL DEFAULT 'active',
              updated_at TEXT NOT NULL
            );
            """
        )


def test_graph_fingerprint_ignores_build_identity_and_timestamps(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    create_schema(database)
    writer = GraphWriter(database)
    writer.upsert_node(
        Node(
            node_id="JAVA_CLASS:demo.Service",
            node_type="JAVA_CLASS",
            display_name="Service",
            extractor="test",
        )
    )
    writer.upsert_node(
        Node(
            node_id="GRAPH_BUILD:first",
            node_type="GRAPH_BUILD",
            display_name="first",
            extractor="test",
        )
    )
    writer.upsert_edge(
        Edge(
            edge_type="CONTAINS_BUILD_OUTPUT",
            from_node_id="GRAPH_BUILD:first",
            to_node_id="JAVA_CLASS:demo.Service",
            extractor="test",
        )
    )
    writer.close()
    first = graph_semantic_fingerprint(database)

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE node SET updated_at='2099-01-01'")
        connection.execute("UPDATE edge SET updated_at='2099-01-01'")
        connection.execute(
            "DELETE FROM edge WHERE from_node_id='GRAPH_BUILD:first'"
        )
        connection.execute("DELETE FROM node WHERE node_type='GRAPH_BUILD'")
        connection.execute(
            """
            INSERT INTO node (
              node_id, node_type, display_name, extractor,
              extractor_version, updated_at
            ) VALUES ('GRAPH_BUILD:second', 'GRAPH_BUILD', 'second',
                      'test', '1', '2099-01-01')
            """
        )
        connection.execute(
            """
            INSERT INTO edge (
              edge_id, edge_type, from_node_id, to_node_id,
              extractor, extractor_version, updated_at
            ) VALUES ('build-edge-second', 'CONTAINS_BUILD_OUTPUT',
                      'GRAPH_BUILD:second', 'JAVA_CLASS:demo.Service',
                      'test', '1', '2099-01-01')
            """
        )
    assert graph_semantic_fingerprint(database) == first

    writer = GraphWriter(database)
    writer.upsert_node(
        Node(
            node_id="JAVA_CLASS:demo.Other",
            node_type="JAVA_CLASS",
            display_name="Other",
            extractor="test",
        )
    )
    writer.upsert_edge(
        Edge(
            edge_type="EXTENDS",
            from_node_id="JAVA_CLASS:demo.Other",
            to_node_id="JAVA_CLASS:demo.Service",
            extractor="test",
        )
    )
    writer.close()

    assert graph_semantic_fingerprint(database) != first


def test_semantic_fingerprints_exclude_run_timestamps(tmp_path: Path) -> None:
    database = tmp_path / "semantic.db"
    create_schema(database)
    apply_migrations(database, Path(__file__).resolve().parents[2] / "storage/migrations")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO extraction_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "run-1", "call_graph", "d" * 64, "s" * 64, "aosp", "userdebug",
                "[\"services\"]", "2.26.3", "java-kotlin", "l" * 64, 1, 1,
                "complete", "2026-08-20T00:00:00Z", "2026-08-20T01:00:00Z", "{}",
            ),
        )
    first = graph_semantic_fingerprints(database)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE extraction_run SET started_at='2099-01-01', completed_at='2099-01-02'"
        )

    assert graph_semantic_fingerprints(database) == first
