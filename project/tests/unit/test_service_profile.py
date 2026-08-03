from __future__ import annotations

import sqlite3
from pathlib import Path

from graph.writer import Edge, GraphWriter, Node
from scripts.profile_service_registration import (
    prepare_profile_database,
    profile_passes,
    service_graph_fingerprint,
)


def create_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
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
    connection.close()


def test_service_graph_fingerprint_ignores_timestamps_but_detects_facts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    create_schema(database)
    writer = GraphWriter(database)
    writer.upsert_node(
        Node(
            node_id="SERVICE_REGISTRATION:demo",
            node_type="SERVICE_REGISTRATION",
            display_name="demo",
            extractor="test",
        )
    )
    writer.upsert_node(
        Node(
            node_id="BINDER_SERVICE_NAME:demo",
            node_type="BINDER_SERVICE_NAME",
            display_name="demo",
            extractor="test",
        )
    )
    writer.upsert_edge(
        Edge(
            edge_type="REGISTERS_BINDER_NAME",
            from_node_id="SERVICE_REGISTRATION:demo",
            to_node_id="BINDER_SERVICE_NAME:demo",
            extractor="test",
        )
    )
    writer.close()

    first = service_graph_fingerprint(database)
    connection = sqlite3.connect(database)
    connection.execute("UPDATE node SET updated_at='2099-01-01'")
    connection.commit()
    connection.close()
    second = service_graph_fingerprint(database)

    assert second == first

    writer = GraphWriter(database)
    writer.upsert_node(
        Node(
            node_id="LOCAL_SERVICE_KEY:demo.Local",
            node_type="LOCAL_SERVICE_KEY",
            display_name="Local",
            extractor="test",
        )
    )
    writer.close()

    assert service_graph_fingerprint(database) != first


def test_prepare_profile_database_removes_only_existing_service_layer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    create_schema(source)
    writer = GraphWriter(source)
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
            node_id="SERVICE_REGISTRATION:old",
            node_type="SERVICE_REGISTRATION",
            display_name="old",
            extractor="test",
        )
    )
    writer.upsert_node(
        Node(
            node_id="BINDER_SERVICE_NAME:old",
            node_type="BINDER_SERVICE_NAME",
            display_name="old",
            extractor="test",
        )
    )
    writer.upsert_edge(
        Edge(
            edge_type="REGISTERS_BINDER_NAME",
            from_node_id="SERVICE_REGISTRATION:old",
            to_node_id="BINDER_SERVICE_NAME:old",
            extractor="test",
        )
    )
    writer.close()

    prepare_profile_database(source, target)

    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM node WHERE node_type='JAVA_CLASS'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM node").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM edge").fetchone()[0] == 0


def test_profile_gate_requires_identity_diagnostics_and_runtime_improvement() -> None:
    passing = {
        "fingerprint_unchanged": True,
        "acceptance_unchanged": True,
        "diagnostics_unchanged": True,
        "runtime_improved": True,
    }

    assert profile_passes(passing)
    for key in tuple(passing):
        failing = dict(passing)
        failing[key] = False
        assert not profile_passes(failing)
