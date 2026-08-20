from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable


NODE_QUERY = """
SELECT node_id, node_type, qualified_name, display_name, properties_json,
       source_path, line_start, line_end, source_revision, extractor,
       extractor_version, content_hash, status
FROM node
WHERE node_type != 'GRAPH_BUILD'
ORDER BY node_id
"""

EDGE_QUERY = """
SELECT edge_id, edge_type, from_node_id, to_node_id, properties_json,
       source_path, line_start, line_end, source_revision, extractor,
       extractor_version, content_hash, status
FROM edge
WHERE from_node_id NOT LIKE 'GRAPH_BUILD:%'
  AND to_node_id NOT LIKE 'GRAPH_BUILD:%'
ORDER BY edge_id
"""

SEMANTIC_GROUPS = {
    "call_graph": (
        "semantic_definition",
        "call_site",
        "call_target",
    ),
    "interprocedural_dataflow": (
        "program_value",
        "dataflow_path",
        "dataflow_step",
    ),
    "security_trace": (
        "security_trace",
        "security_trace_step",
    ),
    "correction_effective_view": (
        "fact_correction",
        "correction_application",
        "effective_node",
        "effective_edge",
    ),
}

VOLATILE_COLUMNS = frozenset(
    {
        "run_id",
        "started_at",
        "completed_at",
        "updated_at",
        "raw_result_path",
    }
)


def _update_rows(
    digest: "hashlib._Hash",
    label: str,
    rows: Iterable[tuple[object, ...]],
) -> None:
    digest.update(label.encode("ascii"))
    digest.update(b"\n")
    for row in rows:
        digest.update(
            json.dumps(
                row,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")


def graph_semantic_fingerprint(database: Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA query_only=ON")
        _update_rows(digest, "node", connection.execute(NODE_QUERY))
        _update_rows(digest, "edge", connection.execute(EDGE_QUERY))
    return digest.hexdigest()


def _objects(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _stable_table_rows(
    connection: sqlite3.Connection, table: str
) -> Iterable[tuple[object, ...]]:
    columns = [
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({_quoted(table)})")
        if str(row[1]) not in VOLATILE_COLUMNS
    ]
    if not columns:
        return ()
    selection = ",".join(_quoted(column) for column in columns)
    return connection.execute(
        f"SELECT {selection} FROM {_quoted(table)} ORDER BY {selection}"
    )


def _group_fingerprint(
    connection: sqlite3.Connection, tables: Iterable[str]
) -> str:
    digest = hashlib.sha256()
    existing = _objects(connection)
    for table in tables:
        if table in existing:
            _update_rows(digest, table, _stable_table_rows(connection, table))
    return digest.hexdigest()


def graph_semantic_fingerprints(database: Path) -> dict[str, str]:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA query_only=ON")
        result = {
            name: _group_fingerprint(connection, tables)
            for name, tables in SEMANTIC_GROUPS.items()
        }
        whole = hashlib.sha256()
        whole.update(graph_semantic_fingerprint(database).encode("ascii"))
        for name in sorted(result):
            whole.update(name.encode("ascii"))
            whole.update(result[name].encode("ascii"))
        result["whole_graph"] = whole.hexdigest()
        return result


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Hash all stable graph semantics while excluding volatile "
            "GRAPH_BUILD identity and updated_at timestamps"
        )
    )
    parser.add_argument("--db", type=Path, required=True)
    parsed = parser.parse_args(arguments)
    for name, digest in graph_semantic_fingerprints(parsed.db).items():
        print(f"{name}={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
