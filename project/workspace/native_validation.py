from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from collectors.facts.materializer import MaterializationReport


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, int]


_ENDPOINTS = {
    "JNI_BINDS_TO": (
        {"MANAGED_NATIVE_DECLARATION", "JAVA_METHOD", "KOTLIN_METHOD"},
        {"NATIVE_FUNCTION", "CPP_FUNCTION", "C_FUNCTION", "RUST_FUNCTION"},
    ),
    "DEPENDS_ON": ({"SOONG_MODULE"}, {"SOONG_MODULE"}),
    "USES_DEFAULTS": ({"SOONG_MODULE"}, {"SOONG_MODULE"}),
    "MATERIALIZED_AS": ({"SOONG_MODULE"}, {"BUILD_ACTION"}),
    "CONSUMES": ({"BUILD_ACTION"}, {"BUILD_ARTIFACT"}),
    "PRODUCES": ({"BUILD_ACTION"}, {"BUILD_ARTIFACT"}),
}

_CAPABILITY_EVIDENCE = {
    "native_symbols": ("node", "NATIVE_FUNCTION"),
    "native_types": ("node", "CPP_TYPE"),
    "native_includes": ("edge", "INCLUDES"),
    "rust_ffi": ("edge", "EXPORTS_C_ABI_SYMBOL"),
    "jni_bindings": ("edge", "JNI_BINDS_TO"),
    "soong_build_graph": ("node", "SOONG_MODULE"),
    "ninja_build_graph": ("node", "BUILD_ACTION"),
}


def validate_native_graph(
    database: Path,
    reports: Iterable[MaterializationReport],
    strict_capabilities: Iterable[str],
) -> ValidationReport:
    del reports
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, int] = {}
    with sqlite3.connect(database) as connection:
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            errors.append(f"foreign-key violations: {violations[:5]}")
        metrics["effective_candidates"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM effective_node WHERE node_type='EXTRACTION_CANDIDATE'"
            ).fetchone()[0]
        )
        if metrics["effective_candidates"]:
            errors.append("extraction candidates leaked into effective_node")

        for edge_type, (allowed_from, allowed_to) in _ENDPOINTS.items():
            rows = connection.execute(
                """
                SELECT e.edge_id, source.node_type, target.node_type
                FROM effective_edge e
                JOIN effective_node source ON source.node_id=e.from_node_id
                JOIN effective_node target ON target.node_id=e.to_node_id
                WHERE e.edge_type=?
                """,
                (edge_type,),
            ).fetchall()
            for edge_id, from_type, to_type in rows:
                if from_type not in allowed_from or to_type not in allowed_to:
                    errors.append(
                        f"invalid endpoints for {edge_type}: {edge_id} "
                        f"{from_type}->{to_type}"
                    )

        exporters: dict[str, list[str]] = {}
        for node_id, raw in connection.execute(
            "SELECT node_id, properties_json FROM effective_node WHERE node_type='NATIVE_FUNCTION'"
        ):
            export = str(json.loads(raw).get("export_name", ""))
            if export:
                exporters.setdefault(export, []).append(str(node_id))
        for export, identities in sorted(exporters.items()):
            if len(identities) > 1:
                errors.append(f"duplicate native exporter {export}: {identities}")

        for capability in strict_capabilities:
            evidence = _CAPABILITY_EVIDENCE.get(capability)
            if evidence is None:
                errors.append(f"unknown strict capability: {capability}")
                continue
            table, fact_kind = evidence
            column = "node_type" if table == "node" else "edge_type"
            count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM effective_{table} WHERE {column}=?",
                    (fact_kind,),
                ).fetchone()[0]
            )
            metrics[f"strict:{capability}"] = count
            if count == 0:
                errors.append(f"strict capability has no validated evidence: {capability}")
    return ValidationReport(not errors, tuple(errors), tuple(warnings), metrics)
