from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "1.0.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(kind: str, identity: str) -> str:
    return f"{kind}:{identity}"


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: str
    display_name: str
    qualified_name: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    extractor: str = "unknown"
    source_revision: str | None = None


@dataclass(frozen=True)
class Edge:
    edge_type: str
    from_node_id: str
    to_node_id: str
    properties: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    extractor: str = "unknown"
    source_revision: str | None = None

    @property
    def edge_id(self) -> str:
        properties_json = json.dumps(
            self.properties,
            ensure_ascii=False,
            sort_keys=True,
        )
        identity = "|".join(
            [
                self.edge_type,
                self.from_node_id,
                self.to_node_id,
                self.source_path or "",
                str(self.line_start or ""),
                str(self.line_end or ""),
                properties_json,
            ]
        )
        return stable_hash(identity)


class GraphWriter:
    def __init__(
        self,
        db_path: Path,
        source_revision: str = "unknown",
    ) -> None:
        self.source_revision = source_revision
        self.c = sqlite3.connect(db_path)
        self.c.execute("PRAGMA foreign_keys=ON")

    def upsert_node(self, node: Node) -> None:
        effective_revision = node.source_revision or self.source_revision
        properties_json = json.dumps(
            node.properties,
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = stable_hash(
            "|".join(
                [
                    node.node_type,
                    node.qualified_name or "",
                    node.display_name,
                    properties_json,
                    node.source_path or "",
                    str(node.line_start or ""),
                    str(node.line_end or ""),
                    effective_revision,
                ]
            )
        )

        self.c.execute(
            """
            INSERT INTO node VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            ON CONFLICT(node_id) DO UPDATE SET
                node_type=excluded.node_type,
                qualified_name=excluded.qualified_name,
                display_name=excluded.display_name,
                properties_json=excluded.properties_json,
                source_path=excluded.source_path,
                line_start=excluded.line_start,
                line_end=excluded.line_end,
                source_revision=excluded.source_revision,
                extractor=excluded.extractor,
                extractor_version=excluded.extractor_version,
                content_hash=excluded.content_hash,
                status='active',
                updated_at=excluded.updated_at
            """,
            (
                node.node_id,
                node.node_type,
                node.qualified_name,
                node.display_name,
                properties_json,
                node.source_path,
                node.line_start,
                node.line_end,
                effective_revision,
                node.extractor,
                EXTRACTOR_VERSION,
                content_hash,
                "active",
                now_iso(),
            ),
        )

    def upsert_symbol_definition(
        self,
        logical_node: Node,
        *,
        repository: str,
        source_path: str,
        line_start: int | None,
        line_end: int | None,
    ) -> str:
        identity = "|".join(
            [
                repository,
                source_path,
                str(line_start or ""),
                str(line_end or ""),
                logical_node.node_id,
            ]
        )
        definition_id = stable_id("SYMBOL_DEFINITION", stable_hash(identity))
        self.upsert_node(
            Node(
                node_id=definition_id,
                node_type="SYMBOL_DEFINITION",
                qualified_name=logical_node.qualified_name,
                display_name=logical_node.display_name,
                properties={
                    "repository": repository,
                    "logical_node_id": logical_node.node_id,
                    "logical_node_type": logical_node.node_type,
                },
                source_path=source_path,
                line_start=line_start,
                line_end=line_end,
                extractor=logical_node.extractor,
                source_revision=logical_node.source_revision,
            )
        )
        self.upsert_edge(
            Edge(
                edge_type="DEFINES_SYMBOL",
                from_node_id=definition_id,
                to_node_id=logical_node.node_id,
                properties={"repository": repository},
                source_path=source_path,
                line_start=line_start,
                line_end=line_end,
                extractor=logical_node.extractor,
                source_revision=logical_node.source_revision,
            )
        )
        rows = self.c.execute(
            """
            SELECT definition.properties_json
            FROM edge link
            JOIN node definition ON definition.node_id=link.from_node_id
            WHERE link.edge_type='DEFINES_SYMBOL'
              AND link.to_node_id=?
            """,
            (logical_node.node_id,),
        ).fetchall()
        repositories = {
            json.loads(raw).get("repository") for (raw,) in rows
        }
        logical_raw = self.c.execute(
            "SELECT properties_json FROM node WHERE node_id=?",
            (logical_node.node_id,),
        ).fetchone()
        logical_properties = json.loads(logical_raw[0] if logical_raw else "{}")
        logical_properties.update(
            {
                "definition_count": len(rows),
                "definition_repository_count": len(repositories),
                "definition_resolution": (
                    "ambiguous" if len(repositories) > 1 else "unique"
                ),
            }
        )
        self.c.execute(
            "UPDATE node SET properties_json=? WHERE node_id=?",
            (
                json.dumps(
                    logical_properties,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                logical_node.node_id,
            ),
        )
        return definition_id

    def upsert_edge(self, edge: Edge) -> None:
        effective_revision = edge.source_revision or self.source_revision
        properties_json = json.dumps(
            edge.properties,
            ensure_ascii=False,
            sort_keys=True,
        )
        content_hash = stable_hash(
            "|".join(
                [
                    edge.edge_type,
                    edge.from_node_id,
                    edge.to_node_id,
                    properties_json,
                    edge.source_path or "",
                    str(edge.line_start or ""),
                    str(edge.line_end or ""),
                    effective_revision,
                ]
            )
        )

        self.c.execute(
            """
            INSERT INTO edge (
                edge_id,
                edge_type,
                from_node_id,
                to_node_id,
                properties_json,
                source_path,
                line_start,
                line_end,
                source_revision,
                extractor,
                extractor_version,
                content_hash,
                status,
                updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(edge_id) DO UPDATE SET
                properties_json=excluded.properties_json,
                source_path=excluded.source_path,
                line_start=excluded.line_start,
                line_end=excluded.line_end,
                source_revision=excluded.source_revision,
                extractor=excluded.extractor,
                extractor_version=excluded.extractor_version,
                content_hash=excluded.content_hash,
                status='active',
                updated_at=excluded.updated_at
            """,
            (
                edge.edge_id,
                edge.edge_type,
                edge.from_node_id,
                edge.to_node_id,
                properties_json,
                edge.source_path,
                edge.line_start,
                edge.line_end,
                effective_revision,
                edge.extractor,
                EXTRACTOR_VERSION,
                content_hash,
                "active",
                now_iso(),
            ),
        )

    def close(self) -> None:
        self.c.commit()
        self.c.close()
