from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from graph.writer import Edge, GraphWriter, Node
from workspace.symbol_collision_validation import (
    SymbolCollisionError,
    validate_symbol_collisions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_semantic_edge_to_ambiguous_symbol_fails_strict_gate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )
    writer = GraphWriter(database)
    duplicate = Node(
        node_id="JAVA_CLASS:common.Duplicate",
        node_type="JAVA_CLASS",
        qualified_name="common.Duplicate",
        display_name="Duplicate",
    )
    child = Node(
        node_id="JAVA_CLASS:demo.Child",
        node_type="JAVA_CLASS",
        qualified_name="demo.Child",
        display_name="Child",
    )
    writer.upsert_node(duplicate)
    writer.upsert_node(child)
    for repository in ("frameworks/base", "vendor/demo"):
        writer.upsert_symbol_definition(
            duplicate,
            repository=repository,
            source_path=f"{repository}/Duplicate.java",
            line_start=1,
            line_end=1,
        )
    writer.upsert_edge(
        Edge(
            edge_type="EXTENDS",
            from_node_id=child.node_id,
            to_node_id=duplicate.node_id,
        )
    )
    writer.close()

    with pytest.raises(SymbolCollisionError, match="ambiguous semantic symbols"):
        validate_symbol_collisions(database, strict=True)
