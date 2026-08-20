from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .identity import ReconciliationResult, reconcile_definition
from .model import (
    CallSiteRecord,
    DataflowPathRecord,
    DefinitionRecord,
    GuardRecord,
    IdentityTransitionRecord,
    NormalizedRecord,
    ProgramValueRecord,
)


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


@dataclass(frozen=True)
class SecurityMaterializationReport:
    program_values: int
    dataflow_paths: int
    guards: int
    identity_transitions: int
    security_traces: int


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


def _logical_method_id(
    connection: sqlite3.Connection,
    symbol_key: str,
) -> str | None:
    rows = connection.execute(
        """
        SELECT DISTINCT logical_method_id
        FROM semantic_definition
        WHERE semantic_symbol_key = ?
          AND resolution_status = 'unique'
          AND logical_method_id IS NOT NULL
        ORDER BY logical_method_id
        """,
        (symbol_key,),
    ).fetchall()
    return str(rows[0][0]) if len(rows) == 1 else None


def _call_site_at(
    connection: sqlite3.Connection,
    *,
    owner_method_id: str,
    source_path: str,
    line: int,
) -> str | None:
    rows = connection.execute(
        """
        SELECT call_site_id
        FROM call_site
        WHERE caller_method_id = ? AND source_path = ? AND line_start = ?
        ORDER BY call_site_id
        """,
        (owner_method_id, source_path, line),
    ).fetchall()
    return str(rows[0][0]) if len(rows) == 1 else None


def _program_value_id(owner_method_id: str, value: ProgramValueRecord) -> str:
    return "PROGRAM_VALUE:" + _digest(
        owner_method_id, value.value_kind, value.parameter_index, value.declared_type,
        value.identity, value.span.repository_path, value.span.source_path,
        value.span.start_line, value.span.start_column, value.span.end_line,
        value.span.end_column,
    )


def _materialize_program_value(
    connection: sqlite3.Connection,
    value: ProgramValueRecord,
    owner_method_id: str,
    run: MaterializationRun,
) -> str:
    value_id = _program_value_id(owner_method_id, value)
    expression_hash = hashlib.sha256(value.identity.encode("utf-8")).hexdigest()
    content_hash = _digest(
        value_id, run.run_id, value.identity, value.value_kind, value.parameter_index,
        value.declared_type,
    )
    properties = {"identity": value.identity, "ordinal": value.ordinal}
    _upsert_node(
        connection, node_id=value_id, node_type="PROGRAM_VALUE",
        qualified_name=value_id, display_name=value.identity, properties=properties,
        source_path=value.span.source_path, line_start=value.span.start_line,
        line_end=value.span.end_line, source_revision=run.source_revision,
        content_hash=content_hash,
    )
    connection.execute(
        """
        INSERT INTO program_value(
          value_id,run_id,owner_method_id,value_kind,parameter_index,declared_type,
          repository,source_path,line_start,column_start,line_end,column_end,
          expression_hash,content_hash,properties_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(value_id) DO UPDATE SET
          run_id=excluded.run_id,
          owner_method_id=excluded.owner_method_id,
          content_hash=excluded.content_hash,
          properties_json=excluded.properties_json
        """,
        (
            value_id, run.run_id, owner_method_id, value.value_kind,
            value.parameter_index, value.declared_type, value.span.repository_path,
            value.span.source_path, value.span.start_line, value.span.start_column,
            value.span.end_line, value.span.end_column, expression_hash, content_hash,
            _json(properties),
        ),
    )
    _upsert_edge(
        connection, edge_type="VALUE_IN_METHOD", from_node_id=value_id,
        to_node_id=owner_method_id, properties={"run_id": run.run_id},
        source_path=value.span.source_path, line_start=value.span.start_line,
        line_end=value.span.end_line, source_revision=run.source_revision,
    )
    return value_id


def _step_kind(value: ProgramValueRecord, count: int) -> str:
    if value.ordinal == 0:
        return "source"
    if value.ordinal == count - 1:
        return "sink"
    if value.value_kind == "return":
        return "return"
    return "argument"


def _materialize_path(
    connection: sqlite3.Connection,
    path: DataflowPathRecord,
    run: MaterializationRun,
) -> tuple[str, str, str, str, int] | None:
    entry_method_id = _logical_method_id(connection, path.entry_symbol_key)
    if entry_method_id is None:
        return None
    value_ids: list[str] = []
    for value in path.steps:
        owner_method_id = _logical_method_id(connection, value.symbol_key)
        if owner_method_id is None:
            return None
        value_ids.append(_materialize_program_value(connection, value, owner_method_id, run))
    path_id = "DATAFLOW_PATH:" + _digest(path.content_hash, run.evidence_id)
    first = path.steps[0]
    last = path.steps[-1]
    sink_method_id = _logical_method_id(connection, last.symbol_key)
    if sink_method_id is None:
        return None
    properties = {
        "database_fingerprint": path.database_fingerprint,
        "entry_symbol_key": path.entry_symbol_key,
        "query_id": path.query_id,
        "query_version": path.query_version,
        "sink_symbol_key": path.sink_symbol_key,
    }
    _upsert_node(
        connection, node_id=path_id, node_type="DATAFLOW_PATH",
        qualified_name=path_id, display_name=path.scenario, properties=properties,
        source_path=last.span.source_path, line_start=first.span.start_line,
        line_end=last.span.end_line, source_revision=run.source_revision,
        content_hash=path.content_hash,
    )
    connection.execute("DELETE FROM dataflow_step WHERE path_id=?", (path_id,))
    connection.execute(
        """
        INSERT INTO dataflow_path(
          path_id,run_id,scenario_id,source_value_id,sink_value_id,path_kind,
          confidence_class,step_count,path_fingerprint,evidence_id,status,
          repository,source_path,properties_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(path_id) DO UPDATE SET
          run_id=excluded.run_id,
          step_count=excluded.step_count,
          path_fingerprint=excluded.path_fingerprint,
          evidence_id=excluded.evidence_id,
          status=excluded.status,
          properties_json=excluded.properties_json
        """,
        (
            path_id, run.run_id, path.scenario, value_ids[0], value_ids[-1],
            "global_value_flow", "codeql_proven", len(value_ids), path.content_hash,
            run.evidence_id, "accepted", last.span.repository_path,
            last.span.source_path, _json(properties),
        ),
    )
    for value, value_id in zip(path.steps, value_ids, strict=True):
        kind = _step_kind(value, len(value_ids))
        connection.execute(
            """
            INSERT INTO dataflow_step VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                path_id, value.ordinal, value_id, kind, value.span.repository_path,
                value.span.source_path, value.span.start_line, value.span.start_column,
                value.span.end_line, value.span.end_column, value.identity,
                _digest(path_id, value.ordinal, value_id, kind),
            ),
        )
    _upsert_edge(
        connection, edge_type="FLOW_SOURCE", from_node_id=path_id,
        to_node_id=value_ids[0], properties={"evidence_id": run.evidence_id},
        source_path=first.span.source_path, line_start=first.span.start_line,
        line_end=first.span.end_line, source_revision=run.source_revision,
    )
    _upsert_edge(
        connection, edge_type="FLOW_SINK", from_node_id=path_id,
        to_node_id=value_ids[-1], properties={"evidence_id": run.evidence_id},
        source_path=last.span.source_path, line_start=last.span.start_line,
        line_end=last.span.end_line, source_revision=run.source_revision,
    )
    return (
        path_id,
        entry_method_id,
        sink_method_id,
        last.span.source_path,
        last.span.start_line,
    )


def _insert_trace_step(
    connection: sqlite3.Connection,
    trace_id: str,
    ordinal: int,
    kind: str,
    *,
    call_site_id: str | None = None,
    dataflow_path_id: str | None = None,
    guard_call_site_id: str | None = None,
    identity_call_site_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO security_trace_step(
          trace_id,ordinal,step_kind,call_site_id,dataflow_path_id,
          guard_call_site_id,identity_call_site_id,content_hash
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            trace_id, ordinal, kind, call_site_id, dataflow_path_id,
            guard_call_site_id, identity_call_site_id,
            _digest(trace_id, ordinal, kind, call_site_id, dataflow_path_id,
                    guard_call_site_id, identity_call_site_id),
        ),
    )


def materialize_security_facts(
    database: Path,
    records: tuple[NormalizedRecord, ...],
    run: MaterializationRun,
) -> SecurityMaterializationReport:
    paths = tuple(record for record in records if isinstance(record, DataflowPathRecord))
    guards = tuple(record for record in records if isinstance(record, GuardRecord))
    identities = tuple(
        record for record in records if isinstance(record, IdentityTransitionRecord)
    )
    connection = sqlite3.connect(database)
    value_count = 0
    path_count = 0
    guard_count = 0
    identity_count = 0
    trace_count = 0
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for path in paths:
            materialized = _materialize_path(connection, path, run)
            if materialized is None:
                continue
            path_id, entry_method_id, sink_method_id, source_path, sink_line = materialized
            sink_call_site_id = _call_site_at(
                connection, owner_method_id=sink_method_id,
                source_path=source_path, line=sink_line,
            )
            value_count += len(path.steps)
            path_count += 1
            if sink_call_site_id is None:
                continue
            connection.execute(
                """
                DELETE FROM edge
                WHERE from_node_id = ?
                  AND edge_type IN (
                    'GUARDED_BY','IDENTITY_CLEARED_BY','IDENTITY_RESTORED_BY'
                  )
                """,
                (sink_call_site_id,),
            )
            matching_guards: list[tuple[GuardRecord, str]] = []
            for guard in guards:
                owner = _logical_method_id(connection, guard.owner_symbol_key)
                if owner != sink_method_id or guard.source_path != source_path:
                    continue
                guard_site = _call_site_at(
                    connection, owner_method_id=sink_method_id,
                    source_path=source_path, line=guard.guard_line,
                )
                if guard_site is not None and guard.sink_line == sink_line:
                    matching_guards.append((guard, guard_site))
            matching_identities: list[tuple[IdentityTransitionRecord, str, str | None]] = []
            for identity in identities:
                owner = _logical_method_id(connection, identity.owner_symbol_key)
                if owner != sink_method_id or identity.source_path != source_path:
                    continue
                clear_site = _call_site_at(
                    connection, owner_method_id=sink_method_id,
                    source_path=source_path, line=identity.clear_line,
                )
                restore_site = (
                    _call_site_at(
                        connection, owner_method_id=sink_method_id,
                        source_path=source_path, line=identity.restore_line,
                    )
                    if identity.restore_line is not None else None
                )
                if clear_site is not None:
                    matching_identities.append((identity, clear_site, restore_site))
            if any(item[0].status != "paired_all_exits" for item in matching_identities):
                status = "identity_unpaired"
            elif matching_guards:
                status = "guarded"
            elif matching_identities:
                status = "identity_paired"
            else:
                status = "unguarded"
            trace_id = "SECURITY_TRACE:" + _digest(
                path_id, entry_method_id, sink_call_site_id, run.run_id
            )
            trace_properties = {
                "evidence_id": run.evidence_id,
                "path_id": path_id,
                "query_versions": sorted(
                    {path.query_version}
                    | {guard.query_version for guard, _ in matching_guards}
                    | {identity.query_version for identity, _, _ in matching_identities}
                ),
            }
            _upsert_node(
                connection, node_id=trace_id, node_type="SECURITY_TRACE",
                qualified_name=trace_id, display_name=path.scenario,
                properties=trace_properties, source_path=source_path,
                line_start=sink_line, line_end=sink_line,
                source_revision=run.source_revision,
                content_hash=_digest(trace_id, status, trace_properties),
            )
            connection.execute("DELETE FROM security_trace_step WHERE trace_id=?", (trace_id,))
            connection.execute(
                """
                INSERT INTO security_trace(
                  trace_id,run_id,scenario_id,entry_method_id,sink_call_site_id,
                  guard_count,identity_transition_count,trace_fingerprint,status,
                  repository,source_path,properties_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(trace_id) DO UPDATE SET
                  guard_count=excluded.guard_count,
                  identity_transition_count=excluded.identity_transition_count,
                  trace_fingerprint=excluded.trace_fingerprint,
                  status=excluded.status,
                  properties_json=excluded.properties_json
                """,
                (
                    trace_id, run.run_id, path.scenario, entry_method_id,
                    sink_call_site_id, len(matching_guards), len(matching_identities),
                    _digest(
                        path.content_hash,
                        [guard.content_hash for guard, _ in matching_guards],
                        [identity.content_hash for identity, _, _ in matching_identities],
                    ),
                    status, path.steps[-1].span.repository_path, source_path,
                    _json(trace_properties),
                ),
            )
            _upsert_edge(
                connection, edge_type="TRACE_ENTRY", from_node_id=trace_id,
                to_node_id=entry_method_id, properties={"run_id": run.run_id},
                source_path=source_path, line_start=sink_line, line_end=sink_line,
                source_revision=run.source_revision,
            )
            _upsert_edge(
                connection, edge_type="TRACE_SINK", from_node_id=trace_id,
                to_node_id=sink_call_site_id, properties={"run_id": run.run_id},
                source_path=source_path, line_start=sink_line, line_end=sink_line,
                source_revision=run.source_revision,
            )
            ordinal = 0
            _insert_trace_step(
                connection, trace_id, ordinal, "call_site", call_site_id=sink_call_site_id
            )
            ordinal += 1
            _insert_trace_step(
                connection, trace_id, ordinal, "dataflow_path", dataflow_path_id=path_id
            )
            ordinal += 1
            for guard, guard_site in matching_guards:
                _upsert_edge(
                    connection, edge_type="GUARDED_BY", from_node_id=sink_call_site_id,
                    to_node_id=guard_site,
                    properties={"relation_kind": guard.relation_kind, "run_id": run.run_id},
                    source_path=source_path, line_start=guard.guard_line,
                    line_end=guard.guard_line, source_revision=run.source_revision,
                )
                _insert_trace_step(
                    connection, trace_id, ordinal, "guard", guard_call_site_id=guard_site
                )
                ordinal += 1
                guard_count += 1
            for identity, clear_site, restore_site in matching_identities:
                _upsert_edge(
                    connection, edge_type="IDENTITY_CLEARED_BY",
                    from_node_id=sink_call_site_id, to_node_id=clear_site,
                    properties={"status": identity.status, "run_id": run.run_id},
                    source_path=source_path, line_start=identity.clear_line,
                    line_end=identity.clear_line, source_revision=run.source_revision,
                )
                _insert_trace_step(
                    connection, trace_id, ordinal, "identity_clear",
                    identity_call_site_id=clear_site,
                )
                ordinal += 1
                if identity.status == "paired_all_exits" and restore_site is not None:
                    _upsert_edge(
                        connection, edge_type="IDENTITY_RESTORED_BY",
                        from_node_id=sink_call_site_id, to_node_id=restore_site,
                        properties={"status": identity.status, "run_id": run.run_id},
                        source_path=source_path, line_start=identity.restore_line or 1,
                        line_end=identity.restore_line or 1,
                        source_revision=run.source_revision,
                    )
                    _insert_trace_step(
                        connection, trace_id, ordinal, "identity_restore",
                        identity_call_site_id=restore_site,
                    )
                    ordinal += 1
                identity_count += 1
            trace_count += 1
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"foreign-key violations: {violations[:5]}")
        connection.commit()
        return SecurityMaterializationReport(
            value_count, path_count, guard_count, identity_count, trace_count
        )
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
