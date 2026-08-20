from __future__ import annotations

import csv
import io
import json
from collections import OrderedDict
from typing import Iterable
from urllib.parse import unquote, urlparse

from .model import (
    CallSiteRecord,
    CallTargetRecord,
    DataflowPathRecord,
    DefinitionRecord,
    GuardRecord,
    IdentityTransitionRecord,
    NormalizedRecord,
    ProgramValueRecord,
    RecordError,
    SourceSpan,
)


class DecodeError(ValueError):
    pass


CALL_COLUMNS = {
    "schema_version", "record_kind", "language", "package_name",
    "declaring_type", "callable_kind", "callable_name", "erased_parameters",
    "return_type", "repository_path", "source_path", "start_line",
    "start_column", "end_line", "end_column", "caller_symbol_key",
    "callee_symbol_key", "dispatch_kind", "relation_kind", "candidate_count",
    "expression_text", "unresolved_reason",
}

QUERY_COLUMNS = {
    "CallSites": CALL_COLUMNS,
    "SystemServiceDataflow": {
        "schema_version", "scenario", "entry_symbol_key", "source_parameter_index",
        "source_value", "source_repository_path", "source_path", "source_line",
        "source_column_start", "source_column_end", "sink_owner_symbol_key",
        "sink_callable", "sink_repository_path", "sink_path", "sink_line",
        "sink_column_start", "sink_column_end", "source_identity", "sink_identity",
    },
    "SystemServiceGuards": {
        "schema_version", "owner_symbol_key", "guard_callable", "guard_line",
        "sink_callable", "sink_line", "relation_kind", "source_path",
    },
    "BinderIdentity": {
        "schema_version", "owner_symbol_key", "clear_line", "restore_line",
        "transition_status", "source_path",
    },
}


def _integer(row: dict[str, str], name: str) -> int:
    try:
        return int(row[name])
    except (KeyError, TypeError, ValueError) as error:
        raise DecodeError(f"invalid integer column {name!r}: {row.get(name)!r}") from error


def _rows(query_id: str, csv_text: str) -> list[dict[str, str]]:
    if query_id not in QUERY_COLUMNS:
        raise DecodeError(f"unsupported query ID: {query_id}")
    try:
        reader = csv.DictReader(io.StringIO(csv_text), strict=True)
        if reader.fieldnames is None:
            raise DecodeError("CSV has no header")
        missing = QUERY_COLUMNS[query_id] - set(reader.fieldnames)
        if missing:
            raise DecodeError(f"missing required columns: {sorted(missing)!r}")
        rows = list(reader)
    except csv.Error as error:
        raise DecodeError(f"invalid CSV: {error}") from error
    for index, row in enumerate(rows, start=2):
        if None in row:
            raise DecodeError(f"invalid CSV row {index}: surplus columns")
        if row["schema_version"] != "1":
            raise DecodeError(
                f"unsupported schema_version at row {index}: {row['schema_version']!r}"
            )
    return rows


def _parameters(value: str) -> tuple[str, ...]:
    return tuple(item for item in value.split(",") if item)


def _span(row: dict[str, str]) -> SourceSpan:
    try:
        return SourceSpan(
            repository_path=row["repository_path"],
            source_path=row["source_path"],
            start_line=_integer(row, "start_line"),
            start_column=_integer(row, "start_column"),
            end_line=_integer(row, "end_line"),
            end_column=_integer(row, "end_column"),
        )
    except RecordError as error:
        raise DecodeError(f"invalid source span: {error}") from error


def _decode_calls(
    rows: Iterable[dict[str, str]], query_version: str, database_fingerprint: str
) -> tuple[NormalizedRecord, ...]:
    definitions: list[DefinitionRecord] = []
    grouped: "OrderedDict[tuple[object, ...], list[dict[str, str]]]" = OrderedDict()
    for row in rows:
        kind = row["record_kind"]
        if kind == "definition":
            definitions.append(
                DefinitionRecord(
                    language=row["language"], package_name=row["package_name"],
                    declaring_type=row["declaring_type"], callable_kind=row["callable_kind"],
                    callable_name=row["callable_name"],
                    erased_parameters=_parameters(row["erased_parameters"]),
                    return_type=row["return_type"], symbol_key=row["caller_symbol_key"],
                    span=_span(row), query_id="CallSites", query_version=query_version,
                    database_fingerprint=database_fingerprint,
                )
            )
        elif kind == "call":
            relation = row["relation_kind"]
            if relation not in {"must", "may", "unresolved"}:
                raise DecodeError(f"invalid relation_kind: {relation!r}")
            span = _span(row)
            key = (
                row["language"], row["caller_symbol_key"], row["dispatch_kind"], relation,
                _integer(row, "candidate_count"), row["expression_text"],
                row["unresolved_reason"], span,
            )
            grouped.setdefault(key, []).append(row)
        else:
            raise DecodeError(f"invalid record_kind: {kind!r}")
    sites: list[CallSiteRecord] = []
    for key, site_rows in grouped.items():
        language, caller, dispatch, relation, candidate_count, expression, reason, span = key
        targets = tuple(
            sorted(
                {
                    CallTargetRecord(row["callee_symbol_key"], row["relation_kind"])
                    for row in site_rows
                    if row["callee_symbol_key"]
                },
                key=lambda item: item.callee_symbol_key,
            )
        )
        try:
            sites.append(
                CallSiteRecord(
                    language=str(language), caller_symbol_key=str(caller),
                    dispatch_kind=str(dispatch), relation_kind=str(relation),
                    candidate_count=int(candidate_count), expression_text=str(expression),
                    unresolved_reason=str(reason), span=span, targets=targets,
                    query_id="CallSites", query_version=query_version,
                    database_fingerprint=database_fingerprint,
                )
            )
        except RecordError as error:
            raise DecodeError(f"invalid call-site record: {error}") from error
    return tuple(definitions) + tuple(sites)


def _line_span(
    repository_path: str,
    source_path: str,
    line: int,
    column_start: int,
    column_end: int,
) -> SourceSpan:
    return SourceSpan(
        repository_path,
        source_path,
        line,
        column_start,
        line,
        column_end,
    )


def _decode_dataflow(
    rows: Iterable[dict[str, str]], query_version: str, database_fingerprint: str
) -> tuple[NormalizedRecord, ...]:
    result: list[DataflowPathRecord] = []
    for row in rows:
        source_span = _line_span(
            row["source_repository_path"],
            row["source_path"],
            _integer(row, "source_line"),
            _integer(row, "source_column_start"),
            _integer(row, "source_column_end"),
        )
        sink_span = _line_span(
            row["sink_repository_path"],
            row["sink_path"],
            _integer(row, "sink_line"),
            _integer(row, "sink_column_start"),
            _integer(row, "sink_column_end"),
        )
        source = ProgramValueRecord(
            row["source_identity"], row["entry_symbol_key"], "parameter", 0, source_span,
            parameter_index=_integer(row, "source_parameter_index"),
        )
        sink = ProgramValueRecord(
            row["sink_identity"], row["sink_owner_symbol_key"], "expression", 1, sink_span
        )
        result.append(
            DataflowPathRecord(
                scenario=row["scenario"], entry_symbol_key=row["entry_symbol_key"],
                sink_symbol_key=row["sink_owner_symbol_key"] + "#" + row["sink_callable"],
                steps=(source, sink), query_id="SystemServiceDataflow",
                query_version=query_version, database_fingerprint=database_fingerprint,
            )
        )
    return tuple(result)


def _path_metadata(message: str) -> dict[str, str]:
    parts = message.split(";")
    if not parts or parts[0] != "ACI1":
        raise DecodeError("invalid CodeQL path metadata version")
    result: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            raise DecodeError(f"invalid CodeQL path metadata item: {part!r}")
        key, value = part.split("=", 1)
        if not key or key in result:
            raise DecodeError(f"invalid duplicate CodeQL path metadata key: {key!r}")
        result[key] = value
    required = {
        "scenario", "entry", "source_parameter_index", "source_repository",
        "source_path", "sink_owner", "sink_callable", "sink_repository",
        "sink_path",
    }
    missing = required - set(result)
    if missing:
        raise DecodeError(f"missing CodeQL path metadata: {sorted(missing)!r}")
    return result


def _source_path_from_uri(uri: str, repository_paths: tuple[str, ...]) -> str:
    parsed = urlparse(uri)
    raw_path = unquote(parsed.path if parsed.scheme == "file" else uri)
    normalized = raw_path.replace("\\", "/")
    for repository in sorted(repository_paths, key=len, reverse=True):
        marker = "/" + repository.strip("/") + "/"
        index = normalized.find(marker)
        if index >= 0:
            return normalized[index + 1 :]
        if normalized.lstrip("/").startswith(repository.strip("/") + "/"):
            return normalized.lstrip("/")
    raise DecodeError(f"path location is outside configured repositories: {uri!r}")


def _repository_for_path(source_path: str, repository_paths: tuple[str, ...]) -> str:
    matches = [
        repository
        for repository in repository_paths
        if source_path == repository or source_path.startswith(repository.rstrip("/") + "/")
    ]
    if not matches:
        raise DecodeError(f"no repository owns CodeQL path location: {source_path!r}")
    return max(matches, key=len)


def decode_sarif_paths(
    sarif_text: str,
    *,
    query_version: str,
    database_fingerprint: str,
    repository_paths: tuple[str, ...],
) -> tuple[DataflowPathRecord, ...]:
    try:
        payload = json.loads(sarif_text)
        runs = payload["runs"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise DecodeError(f"invalid CodeQL path SARIF: {error}") from error
    if not isinstance(runs, list) or len(runs) != 1:
        raise DecodeError("CodeQL path SARIF must contain exactly one run")
    result: list[DataflowPathRecord] = []
    try:
        results = runs[0].get("results", [])
        for finding in results:
            metadata = _path_metadata(str(finding["message"]["text"]))
            for code_flow in finding.get("codeFlows", []):
                for thread_flow in code_flow.get("threadFlows", []):
                    locations = thread_flow.get("locations", [])
                    if len(locations) < 2:
                        raise DecodeError("CodeQL path explanation has fewer than two nodes")
                    steps: list[ProgramValueRecord] = []
                    for ordinal, item in enumerate(locations):
                        location = item["location"]
                        physical = location["physicalLocation"]
                        uri = str(physical["artifactLocation"]["uri"])
                        region = physical["region"]
                        source_path = _source_path_from_uri(uri, repository_paths)
                        repository = _repository_for_path(source_path, repository_paths)
                        line = int(region["startLine"])
                        start_column = int(region.get("startColumn", 1))
                        end_line = int(region.get("endLine", line))
                        end_column = int(region.get("endColumn", start_column))
                        symbol_key = ""
                        value_kind = "expression"
                        parameter_index: int | None = None
                        if ordinal == 0:
                            symbol_key = metadata["entry"]
                            value_kind = "parameter"
                            parameter_index = int(metadata["source_parameter_index"])
                        elif ordinal == len(locations) - 1:
                            symbol_key = metadata["sink_owner"]
                        steps.append(
                            ProgramValueRecord(
                                identity=str(location.get("message", {}).get("text", "")),
                                symbol_key=symbol_key,
                                value_kind=value_kind,
                                ordinal=ordinal,
                                span=SourceSpan(
                                    repository, source_path, line, start_column,
                                    end_line, end_column,
                                ),
                                parameter_index=parameter_index,
                            )
                        )
                    result.append(
                        DataflowPathRecord(
                            scenario=metadata["scenario"],
                            entry_symbol_key=metadata["entry"],
                            sink_symbol_key=(
                                metadata["sink_owner"] + "#" + metadata["sink_callable"]
                            ),
                            steps=tuple(steps),
                            query_id="SystemServiceDataflow",
                            query_version=query_version,
                            database_fingerprint=database_fingerprint,
                        )
                    )
    except (KeyError, TypeError, ValueError, RecordError) as error:
        if isinstance(error, DecodeError):
            raise
        raise DecodeError(f"invalid CodeQL path SARIF result: {error}") from error
    return tuple(result)


def _decode_guards(
    rows: Iterable[dict[str, str]], query_version: str, database_fingerprint: str
) -> tuple[NormalizedRecord, ...]:
    try:
        return tuple(
            GuardRecord(
                owner_symbol_key=row["owner_symbol_key"], guard_callable=row["guard_callable"],
                sink_callable=row["sink_callable"], relation_kind=row["relation_kind"],
                guard_line=_integer(row, "guard_line"), sink_line=_integer(row, "sink_line"),
                source_path=row["source_path"], query_id="SystemServiceGuards",
                query_version=query_version, database_fingerprint=database_fingerprint,
            )
            for row in rows
        )
    except RecordError as error:
        raise DecodeError(f"invalid guard record: {error}") from error


def _decode_identity(
    rows: Iterable[dict[str, str]], query_version: str, database_fingerprint: str
) -> tuple[NormalizedRecord, ...]:
    result: list[IdentityTransitionRecord] = []
    for row in rows:
        restore = _integer(row, "restore_line")
        try:
            result.append(
                IdentityTransitionRecord(
                    owner_symbol_key=row["owner_symbol_key"], clear_line=_integer(row, "clear_line"),
                    restore_line=restore or None, status=row["transition_status"],
                    source_path=row["source_path"], query_id="BinderIdentity",
                    query_version=query_version, database_fingerprint=database_fingerprint,
                )
            )
        except RecordError as error:
            raise DecodeError(f"invalid identity record: {error}") from error
    return tuple(result)


def decode_csv(
    query_id: str,
    csv_text: str,
    *,
    query_version: str,
    database_fingerprint: str,
) -> tuple[NormalizedRecord, ...]:
    rows = _rows(query_id, csv_text)
    if query_id == "CallSites":
        return _decode_calls(rows, query_version, database_fingerprint)
    if query_id == "SystemServiceDataflow":
        return _decode_dataflow(rows, query_version, database_fingerprint)
    if query_id == "SystemServiceGuards":
        return _decode_guards(rows, query_version, database_fingerprint)
    if query_id == "BinderIdentity":
        return _decode_identity(rows, query_version, database_fingerprint)
    raise DecodeError(f"unsupported query ID: {query_id}")
