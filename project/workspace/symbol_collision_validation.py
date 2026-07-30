from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path


class SymbolCollisionError(RuntimeError):
    pass


def inspect_symbol_collisions(database: Path) -> list[dict[str, object]]:
    try:
        connection = sqlite3.connect(database)
        rows = connection.execute(
            """
            SELECT link.to_node_id, logical.node_type, logical.qualified_name,
                   definition.node_id,
                   json_extract(definition.properties_json, '$.repository'),
                   definition.source_path, definition.line_start
            FROM edge link
            JOIN node definition ON definition.node_id=link.from_node_id
            JOIN node logical ON logical.node_id=link.to_node_id
            WHERE link.edge_type='DEFINES_SYMBOL'
              AND link.to_node_id IN (
                SELECT to_node_id
                FROM edge collision_link
                JOIN node collision_definition
                  ON collision_definition.node_id=collision_link.from_node_id
                WHERE collision_link.edge_type='DEFINES_SYMBOL'
                GROUP BY to_node_id
                HAVING COUNT(DISTINCT json_extract(
                    collision_definition.properties_json, '$.repository'
                )) > 1
              )
            ORDER BY link.to_node_id, definition.node_id
            """
        ).fetchall()
        collision_ids = sorted({str(row[0]) for row in rows})
        semantic: set[str] = set()
        if collision_ids:
            placeholders = ",".join("?" for _ in collision_ids)
            query = f"""
                SELECT from_node_id
                FROM edge
                WHERE edge_type NOT IN ('DEFINES_SYMBOL', 'DECLARED_IN')
                  AND from_node_id IN ({placeholders})
                UNION
                SELECT to_node_id
                FROM edge
                WHERE edge_type NOT IN ('DEFINES_SYMBOL', 'DECLARED_IN')
                  AND to_node_id IN ({placeholders})
            """
            semantic = {
                str(row[0]) for row in connection.execute(
                    query,
                    (*collision_ids, *collision_ids),
                )
            }
    except sqlite3.Error as error:
        raise SymbolCollisionError(f"cannot inspect symbol collisions: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()
    grouped: dict[str, dict[str, object]] = {}
    for (
        logical_id, node_type, qualified_name, definition_id,
        repository, source_path, line_start,
    ) in rows:
        item = grouped.setdefault(
            logical_id,
            {
                "logical_node_id": logical_id,
                "logical_node_type": node_type,
                "qualified_name": qualified_name,
                "affects_semantic_edges": logical_id in semantic,
                "definitions": [],
            },
        )
        item["definitions"].append(
            {
                "definition_id": definition_id,
                "repository": repository,
                "source_path": source_path,
                "line_start": line_start,
            }
        )
    return [grouped[key] for key in sorted(grouped)]


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_symbol_collisions(
    database: Path,
    *,
    strict: bool,
    report: Path | None = None,
) -> list[dict[str, object]]:
    collisions = inspect_symbol_collisions(database)
    if report is not None:
        _atomic_json(report, collisions)
    blocking = [item for item in collisions if item["affects_semantic_edges"]]
    if strict and blocking:
        sample = ", ".join(
            str(item["logical_node_id"]) for item in blocking[:8]
        )
        raise SymbolCollisionError(f"ambiguous semantic symbols: {sample}")
    return collisions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    strict = args.strict
    if args.plan is not None:
        try:
            strict = strict or bool(
                json.loads(args.plan.read_text(encoding="utf-8")).get("strict")
            )
        except (OSError, json.JSONDecodeError) as error:
            print(f"ERROR: invalid execution plan: {error}")
            return 1
    try:
        collisions = validate_symbol_collisions(
            args.db,
            strict=strict,
            report=args.report,
        )
    except SymbolCollisionError as error:
        print(f"ERROR: {error}")
        return 1
    blocking = sum(bool(item["affects_semantic_edges"]) for item in collisions)
    print(f"Symbol collisions: {len(collisions)}; semantic: {blocking}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
