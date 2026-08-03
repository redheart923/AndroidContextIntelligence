from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_NODE_TYPES = (
    "SERVICE_REGISTRATION",
    "BINDER_SERVICE_NAME",
    "LOCAL_SERVICE_KEY",
)
SERVICE_EDGE_TYPES = (
    "REGISTERS_BINDER_NAME",
    "REGISTERS_LOCAL_KEY",
    "REGISTERS_INSTANCE",
    "REGISTERED_AS",
    "EXPOSED_AS_LOCAL_SERVICE",
)


def service_graph_fingerprint(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        nodes = connection.execute(
            f"""
            SELECT node_id, node_type, qualified_name, display_name,
                   properties_json, source_path, line_start, line_end,
                   source_revision, status
            FROM node
            WHERE node_type IN ({','.join('?' for _ in SERVICE_NODE_TYPES)})
            ORDER BY node_id
            """,
            SERVICE_NODE_TYPES,
        ).fetchall()
        edges = connection.execute(
            f"""
            SELECT edge_id, edge_type, from_node_id, to_node_id,
                   properties_json, source_path, line_start, line_end,
                   source_revision, status
            FROM edge
            WHERE edge_type IN ({','.join('?' for _ in SERVICE_EDGE_TYPES)})
            ORDER BY edge_id
            """,
            SERVICE_EDGE_TYPES,
        ).fetchall()
    payload = {"nodes": nodes, "edges": edges}
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def prepare_profile_database(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as source_connection:
        with sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)
    placeholders = ",".join("?" for _ in SERVICE_NODE_TYPES)
    with sqlite3.connect(target) as connection:
        service_node_ids = connection.execute(
            f"SELECT node_id FROM node WHERE node_type IN ({placeholders})",
            SERVICE_NODE_TYPES,
        ).fetchall()
        identifiers = [row[0] for row in service_node_ids]
        if identifiers:
            node_placeholders = ",".join("?" for _ in identifiers)
            connection.execute(
                f"""
                DELETE FROM edge
                WHERE from_node_id IN ({node_placeholders})
                   OR to_node_id IN ({node_placeholders})
                """,
                identifiers + identifiers,
            )
            connection.execute(
                f"DELETE FROM node WHERE node_id IN ({node_placeholders})",
                identifiers,
            )
        connection.commit()


def _acceptance_counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        binder = dict(
            connection.execute(
                """
                SELECT target.qualified_name, COUNT(*)
                FROM edge relation
                JOIN node target ON target.node_id=relation.to_node_id
                WHERE relation.edge_type='REGISTERED_AS'
                  AND target.qualified_name IN ('activity', 'package')
                GROUP BY target.qualified_name
                """
            )
        )
        local = connection.execute(
            "SELECT COUNT(*) FROM edge WHERE edge_type='EXPOSED_AS_LOCAL_SERVICE'"
        ).fetchone()[0]
    return {
        "activity": int(binder.get("activity", 0)),
        "package": int(binder.get("package", 0)),
        "local_services": int(local),
    }


def profile_service_pipeline(
    plan: Path,
    database: Path,
    cache_dir: Path,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="service-profile-") as raw_temp:
        temp = Path(raw_temp)
        cache = temp / "cache"
        results: list[dict[str, object]] = []
        for label in ("cold", "warm"):
            profile_database = temp / f"{label}.db"
            report = temp / f"{label}.json"
            prepare_profile_database(database, profile_database)
            started = time.perf_counter()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workspace.multi_service",
                    "--plan",
                    str(plan),
                    "--db",
                    str(profile_database),
                    "--report",
                    str(report),
                    "--cache-dir",
                    str(cache),
                ],
                cwd=PROJECT_ROOT,
                check=True,
            )
            results.append(
                {
                    "run": label,
                    "elapsed_seconds": time.perf_counter() - started,
                    "graph_fingerprint": service_graph_fingerprint(
                        profile_database
                    ),
                    "acceptance": _acceptance_counts(profile_database),
                    "pipeline_report": json.loads(
                        report.read_text(encoding="utf-8")
                    ),
                }
            )
            profile_database.unlink()
        cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cache, cache_dir, dirs_exist_ok=True)
    cold, warm = results
    elapsed_improvement = cold["elapsed_seconds"] - warm["elapsed_seconds"]
    return {
        "schema_version": "1.0",
        "runs": results,
        "fingerprint_unchanged": (
            cold["graph_fingerprint"] == warm["graph_fingerprint"]
        ),
        "acceptance_unchanged": (
            cold["acceptance"] == warm["acceptance"]
        ),
        "diagnostics_unchanged": (
            cold["pipeline_report"]["summary"]
            == warm["pipeline_report"]["summary"]
        ),
        "runtime_improved": elapsed_improvement > 0,
        "elapsed_improvement_seconds": elapsed_improvement,
        "elapsed_improvement_ratio": (
            elapsed_improvement / cold["elapsed_seconds"]
            if cold["elapsed_seconds"]
            else 0.0
        ),
    }


def profile_passes(result: dict[str, object]) -> bool:
    return all(
        result.get(key) is True
        for key in (
            "fingerprint_unchanged",
            "acceptance_unchanged",
            "diagnostics_unchanged",
            "runtime_improved",
        )
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile Service graph pipeline")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parsed = parser.parse_args(arguments)
    result = profile_service_pipeline(
        parsed.plan,
        parsed.db,
        parsed.cache_dir,
    )
    parsed.output.parent.mkdir(parents=True, exist_ok=True)
    parsed.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not profile_passes(result):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
