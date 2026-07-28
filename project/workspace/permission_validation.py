from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from collectors.permission.report import REQUIRED_REPORT_KEYS


EDGE_ENDPOINT_TYPES = {
    "DECLARES_PERMISSION": ({"FILE"}, {"PERMISSION"}),
    "REQUESTS_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "ALLOWLISTS_PRIVILEGED_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "DENIES_PRIVILEGED_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "DEFAULT_GRANTS_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "REQUIRES_PERMISSION": ({"JAVA_METHOD", "KOTLIN_METHOD"}, {"PERMISSION"}),
    "CHECKS_PERMISSION": ({"JAVA_METHOD", "KOTLIN_METHOD"}, {"PERMISSION"}),
    "ENFORCES_PERMISSION": ({"JAVA_METHOD", "KOTLIN_METHOD"}, {"PERMISSION"}),
}
SEMANTIC_EDGE_TYPES = tuple(sorted(EDGE_ENDPOINT_TYPES))


class PermissionValidationError(RuntimeError):
    pass


def _placeholders(values: Iterable[object]) -> str:
    return ",".join("?" for _ in values)


def validate_permission_report(report_path: Path) -> dict[str, object]:
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PermissionValidationError(
            f"permission report does not exist: {report_path}"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise PermissionValidationError(
            f"invalid permission report: {report_path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise PermissionValidationError("permission report root must be an object")
    missing = sorted(REQUIRED_REPORT_KEYS - set(payload))
    if missing:
        raise PermissionValidationError(f"missing report keys: {', '.join(missing)}")
    return payload


def _active_semantic_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    placeholders = _placeholders(SEMANTIC_EDGE_TYPES)
    return connection.execute(
        f"""
        SELECT e.*, source.node_type AS source_type,
               target.node_type AS target_type
        FROM edge e
        LEFT JOIN node source ON source.node_id=e.from_node_id
        LEFT JOIN node target ON target.node_id=e.to_node_id
        WHERE e.status='active' AND e.edge_type IN ({placeholders})
        ORDER BY e.edge_type, e.from_node_id, e.to_node_id,
                 COALESCE(e.source_path, ''), COALESCE(e.line_start, -1),
                 e.properties_json, e.edge_id
        """,
        SEMANTIC_EDGE_TYPES,
    ).fetchall()


def _validate_aosp_evidence(rows: list[sqlite3.Row], connection: sqlite3.Connection) -> None:
    manage_usb_declaration = connection.execute(
        """
        SELECT 1
        FROM edge declaration
        JOIN node permission
          ON permission.node_id = declaration.to_node_id
        WHERE declaration.status='active'
          AND declaration.edge_type='DECLARES_PERMISSION'
          AND permission.status='active'
          AND permission.node_type='PERMISSION'
          AND permission.qualified_name='android.permission.MANAGE_USB'
        LIMIT 1
        """
    ).fetchone()
    edge_types = {str(row["edge_type"]) for row in rows}
    required = {
        "REQUESTS_PERMISSION",
        "DEFAULT_GRANTS_PERMISSION",
        "CHECKS_PERMISSION",
        "ENFORCES_PERMISSION",
    }
    gaps = sorted(required - edge_types)
    if not ({"ALLOWLISTS_PRIVILEGED_PERMISSION", "DENIES_PRIVILEGED_PERMISSION"} & edge_types):
        gaps.append("ALLOWLISTS_OR_DENIES_PRIVILEGED_PERMISSION")
    default_has_policy = False
    for row in rows:
        if row["edge_type"] != "DEFAULT_GRANTS_PERMISSION":
            continue
        try:
            properties = json.loads(row["properties_json"])
        except json.JSONDecodeError:
            properties = {}
        if properties.get("fixed") is not None or properties.get("whitelisted") is not None:
            default_has_policy = True
            break
    if not default_has_policy:
        gaps.append("DEFAULT_GRANT_FIXED_OR_WHITELISTED")
    if manage_usb_declaration is None:
        gaps.append("DECLARES_PERMISSION:android.permission.MANAGE_USB")
    if gaps:
        raise PermissionValidationError(
            "required AOSP permission evidence is missing: " + ", ".join(gaps)
        )


def validate_permission_database(
    database: Path,
    *,
    require_aosp_evidence: bool = False,
) -> None:
    try:
        connection = sqlite3.connect(database)
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise PermissionValidationError(
                f"foreign key validation failed: {len(foreign_keys)} error(s)"
            )
        rows = _active_semantic_rows(connection)
        seen: set[tuple[object, ...]] = set()
        for row in rows:
            allowed_source, allowed_target = EDGE_ENDPOINT_TYPES[row["edge_type"]]
            if row["source_type"] not in allowed_source or row["target_type"] not in allowed_target:
                raise PermissionValidationError(
                    "invalid endpoint type for "
                    f"{row['edge_type']}: {row['source_type']} -> {row['target_type']}"
                )
            identity = (
                row["edge_type"],
                row["from_node_id"],
                row["to_node_id"],
                row["source_path"],
                row["line_start"],
                row["properties_json"],
            )
            if identity in seen:
                raise PermissionValidationError(
                    f"duplicate active semantic edge: {row['edge_type']}"
                )
            seen.add(identity)
        if require_aosp_evidence:
            _validate_aosp_evidence(rows, connection)
    except sqlite3.Error as error:
        raise PermissionValidationError(
            f"invalid permission database: {database}: {error}"
        ) from error
    finally:
        if "connection" in locals():
            connection.close()


def permission_semantic_fingerprint(database: Path) -> str:
    validate_permission_database(database)
    connection = sqlite3.connect(database)
    try:
        connection.row_factory = sqlite3.Row
        rows = _active_semantic_rows(connection)
        endpoint_ids = sorted(
            {str(row["from_node_id"]) for row in rows}
            | {str(row["to_node_id"]) for row in rows}
        )
        nodes: list[dict[str, object]] = []
        if endpoint_ids:
            placeholders = _placeholders(endpoint_ids)
            node_rows = connection.execute(
                f"""
                SELECT node_id, node_type, qualified_name, display_name,
                       properties_json, source_path, line_start, line_end,
                       source_revision, status
                FROM node WHERE node_id IN ({placeholders})
                ORDER BY node_id
                """,
                endpoint_ids,
            ).fetchall()
            nodes = [dict(row) for row in node_rows]
        edges = [
            {
                key: row[key]
                for key in (
                    "edge_type",
                    "from_node_id",
                    "to_node_id",
                    "properties_json",
                    "source_path",
                    "line_start",
                    "line_end",
                    "source_revision",
                    "status",
                )
            }
            for row in rows
        ]
    finally:
        connection.close()
    canonical = json.dumps(
        {"nodes": nodes, "edges": edges},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Permission Semantics Graph")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--require-aosp-evidence", action="store_true")
    parser.add_argument("--fingerprint", action="store_true")
    parser.add_argument("--fingerprint-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    try:
        if not arguments.fingerprint_only:
            validate_permission_report(arguments.report)
        validate_permission_database(
            arguments.db,
            require_aosp_evidence=arguments.require_aosp_evidence,
        )
        fingerprint = permission_semantic_fingerprint(arguments.db)
    except PermissionValidationError as error:
        print(f"ERROR: {error}")
        return 1
    if arguments.fingerprint or arguments.fingerprint_only:
        print(fingerprint)
    else:
        print("permission_validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
