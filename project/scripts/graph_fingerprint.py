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


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Hash all stable graph semantics while excluding volatile "
            "GRAPH_BUILD identity and updated_at timestamps"
        )
    )
    parser.add_argument("--db", type=Path, required=True)
    parsed = parser.parse_args(arguments)
    print(graph_semantic_fingerprint(parsed.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
