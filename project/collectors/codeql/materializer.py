from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .identity import ReconciliationResult, reconcile_definition
from .model import CallSiteRecord, DefinitionRecord, NormalizedRecord


EXTRACTOR = "codeql-java-kotlin"
EXTRACTOR_VERSION = "0.1.0"


@dataclass(frozen=True)
class MaterializationRun:
    run_id: str
    evidence_id: str
    source_revision: str


@dataclass(frozen=True)
class CallMaterializationReport:
    definitions: int
    call_sites: int
    accepted_targets: int
    ambiguous_sites: int
    unresolved_sites: int
    skipped_callers: int


def _digest(*values: object) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_node(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    node_type: str,
    qualified_name: str,
    display_name: str,
    properties: dict[str, object],
    source_path: str,
    line_start: int,
    line_end: int,
    source_revision: str,
    content_hash: str,
) -> None:
    connection.execute(
        """
        INSERT INTO node(
          node_id,node_type,qualified_name,display_name,properties_json,
          source_path,line_start,line_end,source_revision,extractor,
          extractor_version,content_hash,status,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            node_id, node_type, qualified_name, display_name, _json(properties),
            source_path, line_start, line_end, source_revision, EXTRACTOR,
            EXTRACTOR_VERSION, content_hash, "active", _now(),
        ),
    )


def _upsert_edge(
    connection: sqlite3.Connection,
    *,
    edge_type: str,
    from_node_id: str,
    to_node_id: str,
    properties: dict[str, object],
    source_path: str,
    line_start: int,
    line_end: int,
    source_revision: str,
    semantic_identity: str = "",
) -> None:
    edge_id = _digest(edge_type, from_node_id, to_node_id, semantic_identity)
    properties_json = _json(properties)
    content_hash = _digest(
        edge_type, from_node_id, to_node_id, properties, source_path,
        line_start, line_end, source_revision,
    )
    connection.execute(
        """
        INSERT INTO edge(
          edge_id,edge_type,from_node_id,to_node_id,properties_json,
          source_path,line_start,line_end,source_revision,extractor,
          extractor_version,content_hash,status,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            edge_id, edge_type, from_node_id, to_node_id, properties_json,
            source_path, line_start, line_end, source_revision, EXTRACTOR,
            EXTRACTOR_VERSION, content_hash, "active", _now(),
        ),
    )


def _definition_id(definition: DefinitionRecord) -> str:
    return "SEMANTIC_DEFINITION:" + _digest(
        definition.database_fingerprint,
        definition.query_version,
        definition.symbol_key,
        definition.span.source_path,
        definition.span.start_line,
        definition.span.start_column,
    )


def _call_site_id(call: CallSiteRecord) -> str:
    return "CALL_SITE:" + _digest(
        call.database_fingerprint,
        call.query_version,
        call.span.repository_path,
        call.span.source_path,
        call.span.start_line,
        call.span.start_column,
        call.caller_symbol_key,
        hashlib.sha256(call.expression_text.encode("utf-8")).hexdigest(),
    )


def _resolve_symbol(
    symbol_key: str,
    definitions: dict[str, list[tuple[DefinitionRecord, ReconciliationResult]]],
) -> ReconciliationResult:
    matches = definitions.get(symbol_key, [])
    if not matches:
        return ReconciliationResult("unmatched", (), ("no_semantic_definition", symbol_key))
    ids = tuple(
        sorted(
            {
                logical_id
                for _, result in matches
                for logical_id in result.logical_method_ids
            }
        )
    )
    if any(result.status == "synthetic" for _, result in matches):
        return ReconciliationResult("synthetic", (), ("synthetic_endpoint", symbol_key))
    if len(ids) == 1 and all(result.status == "unique" for _, result in matches):
        return ReconciliationResult("unique", ids, ("semantic_definition",))
    if ids:
        return ReconciliationResult("ambiguous", ids, ("ambiguous_semantic_definition", symbol_key))
    return ReconciliationResult("unmatched", (), ("unmatched_semantic_definition", symbol_key))


def _materialize_definition(
    connection: sqlite3.Connection,
    definition: DefinitionRecord,
    result: ReconciliationResult,
    run: MaterializationRun,
) -> None:
    node_id = _definition_id(definition)
    properties = {
        "database_fingerprint": definition.database_fingerprint,
        "diagnostics": list(result.diagnostics),
        "erased_parameters": list(definition.erased_parameters),
        "package_name": definition.package_name,
        "query_id": definition.query_id,
        "query_version": definition.query_version,
        "return_type": definition.return_type,
    }
    _upsert_node(
        connection, node_id=node_id, node_type="SEMANTIC_DEFINITION",
        qualified_name=definition.symbol_key, display_name=definition.callable_name,
        properties=properties, source_path=definition.span.source_path,
        line_start=definition.span.start_line, line_end=definition.span.end_line,
        source_revision=run.source_revision, content_hash=definition.content_hash,
    )
    logical_method_id = result.logical_method_ids[0] if result.status == "unique" else None
    connection.execute(
        """
        INSERT INTO semantic_definition(
          definition_id,run_id,logical_method_id,semantic_symbol_key,language,
          callable_kind,repository,source_path,line_start,column_start,line_end,
          column_end,resolution_status,content_hash,properties_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(definition_id) DO UPDATE SET
          run_id=excluded.run_id,
          logical_method_id=excluded.logical_method_id,
          resolution_status=excluded.resolution_status,
          content_hash=excluded.content_hash,
          properties_json=excluded.properties_json
        """,
        (
            node_id, run.run_id, logical_method_id, definition.symbol_key,
            definition.language, definition.callable_kind,
            definition.span.repository_path, definition.span.source_path,
            definition.span.start_line, definition.span.start_column,
            definition.span.end_line, definition.span.end_column,
            result.status, definition.content_hash, _json(properties),
        ),
    )


def materialize_call_graph(
    database: Path,
    records: tuple[NormalizedRecord, ...],
    run: MaterializationRun,
) -> CallMaterializationReport:
    definition_records = tuple(
        record for record in records if isinstance(record, DefinitionRecord)
    )
    call_records = tuple(record for record in records if isinstance(record, CallSiteRecord))
    reconciled: dict[str, list[tuple[DefinitionRecord, ReconciliationResult]]] = defaultdict(list)
    for definition in definition_records:
        reconciled[definition.symbol_key].append(
            (definition, reconcile_definition(database, definition))
        )
    accepted_targets = 0
    ambiguous_sites = 0
    unresolved_sites = 0
    skipped_callers = 0
    projected: dict[tuple[str, str, str], list[tuple[str, int]]] = defaultdict(list)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            DELETE FROM edge
            WHERE edge_type = 'CALLS'
              AND extractor = ?
              AND json_extract(properties_json, '$.run_id') = ?
            """,
            (EXTRACTOR, run.run_id),
        )
        for matches in reconciled.values():
            for definition, result in matches:
                _materialize_definition(connection, definition, result, run)
        materialized_sites = 0
        for call in call_records:
            caller = _resolve_symbol(call.caller_symbol_key, reconciled)
            if caller.status != "unique":
                skipped_callers += 1
                continue
            caller_id = caller.logical_method_ids[0]
            target_results = [
                (target, _resolve_symbol(target.callee_symbol_key, reconciled))
                for target in call.targets
            ]
            if call.relation_kind == "unresolved":
                resolution_status = "unresolved"
            elif any(result.status == "ambiguous" for _, result in target_results):
                resolution_status = "ambiguous"
            elif any(result.status != "unique" for _, result in target_results):
                resolution_status = "unresolved"
            else:
                resolution_status = "resolved"
            if resolution_status == "ambiguous":
                ambiguous_sites += 1
            elif resolution_status == "unresolved":
                unresolved_sites += 1
            call_site_id = _call_site_id(call)
            connection.execute(
                """
                DELETE FROM edge
                WHERE from_node_id = ?
                  AND edge_type IN ('IN_METHOD', 'MUST_CALL', 'MAY_CALL')
                """,
                (call_site_id,),
            )
            connection.execute(
                "DELETE FROM call_target WHERE call_site_id = ?",
                (call_site_id,),
            )
            expression_hash = hashlib.sha256(call.expression_text.encode("utf-8")).hexdigest()
            properties = {
                "database_fingerprint": call.database_fingerprint,
                "dispatch_kind": call.dispatch_kind,
                "evidence_id": run.evidence_id,
                "expression_text": call.expression_text,
                "language": call.language,
                "query_id": call.query_id,
                "query_version": call.query_version,
                "relation_kind": call.relation_kind,
                "unresolved_reason": call.unresolved_reason,
            }
            _upsert_node(
                connection, node_id=call_site_id, node_type="CALL_SITE",
                qualified_name=call_site_id, display_name=call.expression_text,
                properties=properties, source_path=call.span.source_path,
                line_start=call.span.start_line, line_end=call.span.end_line,
                source_revision=run.source_revision, content_hash=call.content_hash,
            )
            connection.execute(
                """
                INSERT INTO call_site(
                  call_site_id,run_id,caller_method_id,repository,source_path,
                  line_start,column_start,line_end,column_end,expression_hash,
                  dispatch_kind,resolution_status,candidate_count,content_hash,
                  properties_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(call_site_id) DO UPDATE SET
                  run_id=excluded.run_id,
                  caller_method_id=excluded.caller_method_id,
                  resolution_status=excluded.resolution_status,
                  candidate_count=excluded.candidate_count,
                  content_hash=excluded.content_hash,
                  properties_json=excluded.properties_json
                """,
                (
                    call_site_id, run.run_id, caller_id, call.span.repository_path,
                    call.span.source_path, call.span.start_line, call.span.start_column,
                    call.span.end_line, call.span.end_column, expression_hash,
                    call.dispatch_kind, resolution_status, call.candidate_count,
                    call.content_hash, _json(properties),
                ),
            )
            _upsert_edge(
                connection, edge_type="IN_METHOD", from_node_id=call_site_id,
                to_node_id=caller_id, properties={"run_id": run.run_id},
                source_path=call.span.source_path, line_start=call.span.start_line,
                line_end=call.span.end_line, source_revision=run.source_revision,
            )
            if resolution_status == "resolved":
                for target, result in target_results:
                    callee_id = result.logical_method_ids[0]
                    relation = target.relation_kind
                    target_hash = _digest(call.content_hash, callee_id, relation, run.evidence_id)
                    connection.execute(
                        """
                        INSERT INTO call_target(
                          call_site_id,callee_method_id,relation_kind,evidence_id,content_hash
                        ) VALUES(?,?,?,?,?)
                        ON CONFLICT(call_site_id,callee_method_id,relation_kind)
                        DO UPDATE SET evidence_id=excluded.evidence_id,content_hash=excluded.content_hash
                        """,
                        (call_site_id, callee_id, relation, run.evidence_id, target_hash),
                    )
                    _upsert_edge(
                        connection,
                        edge_type="MUST_CALL" if relation == "must" else "MAY_CALL",
                        from_node_id=call_site_id, to_node_id=callee_id,
                        properties={"evidence_id": run.evidence_id, "run_id": run.run_id},
                        source_path=call.span.source_path, line_start=call.span.start_line,
                        line_end=call.span.end_line, source_revision=run.source_revision,
                        semantic_identity=relation,
                    )
                    projected[(caller_id, callee_id, relation)].append(
                        (call_site_id, call.candidate_count)
                    )
                    accepted_targets += 1
            materialized_sites += 1
        for (caller_id, callee_id, relation), support in sorted(projected.items()):
            properties = {
                "candidate_count": sum(item[1] for item in support),
                "evidence_ids": [run.evidence_id],
                "relation_kind": relation,
                "run_id": run.run_id,
                "supporting_call_site_count": len(support),
                "supporting_call_site_ids": sorted(item[0] for item in support),
            }
            _upsert_edge(
                connection, edge_type="CALLS", from_node_id=caller_id,
                to_node_id=callee_id, properties=properties, source_path="",
                line_start=1, line_end=1, source_revision=run.source_revision,
                semantic_identity=relation,
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"foreign-key violations: {violations[:5]}")
        connection.commit()
        return CallMaterializationReport(
            definitions=len(definition_records), call_sites=materialized_sites,
            accepted_targets=accepted_targets, ambiguous_sites=ambiguous_sites,
            unresolved_sites=unresolved_sites, skipped_callers=skipped_callers,
        )
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
