from __future__ import annotations

import json
from pathlib import Path

import pytest

from collectors.codeql.decode import DecodeError, decode_csv, decode_sarif_paths
from collectors.codeql.model import CallSiteRecord, DataflowPathRecord, DefinitionRecord


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/codeql"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_decode_call_site_preserves_may_candidates_and_unresolved() -> None:
    records = decode_csv(
        "CallSites",
        load_fixture("call-sites.csv"),
        query_version="1",
        database_fingerprint="d" * 64,
    )
    definitions = [record for record in records if isinstance(record, DefinitionRecord)]
    sites = [record for record in records if isinstance(record, CallSiteRecord)]

    assert len(definitions) == 1
    assert len(sites) == 2
    assert sites[0].candidate_count == 2
    assert {target.relation_kind for target in sites[0].targets} == {"may"}
    assert {target.callee_symbol_key for target in sites[0].targets} == {
        "demo.First#run(java.lang.String)",
        "demo.Second#run(java.lang.String)",
    }
    assert sites[1].targets == ()
    assert sites[1].unresolved_reason == "missing_dependency"
    assert sites[0].content_hash == sites[0].content_hash
    assert len(sites[0].content_hash) == 64


def test_decode_rejects_unknown_schema_missing_column_and_invalid_relation() -> None:
    source = load_fixture("call-sites.csv")

    with pytest.raises(DecodeError, match="schema_version"):
        decode_csv(
            "CallSites",
            source.replace("1,definition", "2,definition", 1),
            query_version="1",
            database_fingerprint="d" * 64,
        )

    header, *rows = source.splitlines()
    columns = header.split(",")
    relation_index = columns.index("relation_kind")
    malformed_header = ",".join(
        column for column in columns if column != "source_path"
    )
    with pytest.raises(DecodeError, match="missing required columns"):
        decode_csv(
            "CallSites",
            "\n".join((malformed_header, *rows)),
            query_version="1",
            database_fingerprint="d" * 64,
        )

    invalid = source.replace(",virtual,may,2,", ",virtual,probable,2,", 1)
    assert relation_index > 0
    with pytest.raises(DecodeError, match="relation_kind"):
        decode_csv(
            "CallSites",
            invalid,
            query_version="1",
            database_fingerprint="d" * 64,
        )


def test_decode_rejects_negative_source_span_and_invalid_csv() -> None:
    source = load_fixture("call-sites.csv")
    with pytest.raises(DecodeError, match="source span"):
        decode_csv(
            "CallSites",
            source.replace(",12,7,12,21,", ",-1,7,12,21,", 1),
            query_version="1",
            database_fingerprint="d" * 64,
        )

    with pytest.raises(DecodeError, match="CSV"):
        decode_csv(
            "CallSites",
            source.splitlines()[0] + '\n1,"unterminated',
            query_version="1",
            database_fingerprint="d" * 64,
        )


def test_decode_dataflow_preserves_source_and_sink_repository_spans() -> None:
    csv_text = "\n".join(
        (
            "schema_version,scenario,entry_symbol_key,source_parameter_index,"
            "source_value,source_repository_path,source_path,source_line,"
            "source_column_start,source_column_end,sink_owner_symbol_key,"
            "sink_callable,sink_repository_path,sink_path,sink_line,"
            "sink_column_start,sink_column_end,source_identity,sink_identity",
            "1,binder_argument_to_sensitive_sink,demo.Service#entry(java.lang.String),"
            "0,value,frameworks/base,frameworks/base/demo/Service.java,10,9,14,"
            "demo.Store#write(java.lang.String),demo.Store.write,frameworks/base,"
            "frameworks/base/demo/Store.java,31,5,22,value,write(value)",
        )
    )

    records = decode_csv(
        "SystemServiceDataflow",
        csv_text,
        query_version="1",
        database_fingerprint="d" * 64,
    )

    assert len(records) == 1
    path = records[0]
    assert isinstance(path, DataflowPathRecord)
    assert path.steps[0].span.repository_path == "frameworks/base"
    assert path.steps[0].span.source_path == "frameworks/base/demo/Service.java"
    assert path.steps[0].span.start_line == 10
    assert path.steps[-1].span.repository_path == "frameworks/base"
    assert path.steps[-1].span.source_path == "frameworks/base/demo/Store.java"
    assert path.steps[-1].span.start_line == 31


def test_decode_sarif_preserves_real_ordered_path_locations() -> None:
    message = (
        "ACI1;scenario=binder_argument_to_sensitive_sink;"
        "entry=java|method|demo.Service#entry(java.lang.String);"
        "source_parameter_index=0;"
        "source_repository=frameworks/base;"
        "source_path=frameworks/base/demo/Service.java;"
        "sink_owner=java|method|demo.Store#write(java.lang.String);"
        "sink_callable=demo.Store.write;"
        "sink_repository=frameworks/base;"
        "sink_path=frameworks/base/demo/Store.java"
    )
    locations = []
    for uri, line, text in (
        ("file:///aosp/frameworks/base/demo/Service.java", 10, "value : String"),
        ("file:///aosp/frameworks/base/demo/Helper.java", 20, "forward(value)"),
        ("file:///aosp/frameworks/base/demo/Store.java", 31, "value"),
    ):
        locations.append(
            {
                "location": {
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri},
                        "region": {
                            "startLine": line,
                            "startColumn": 3,
                            "endColumn": 12,
                        },
                    },
                    "message": {"text": text},
                }
            }
        )
    sarif = {
        "runs": [
            {
                "results": [
                    {
                        "message": {"text": message},
                        "codeFlows": [
                            {"threadFlows": [{"locations": locations}]}
                        ],
                    }
                ]
            }
        ]
    }

    records = decode_sarif_paths(
        json.dumps(sarif),
        query_version="1",
        database_fingerprint="d" * 64,
        repository_paths=("frameworks/base",),
    )

    assert len(records) == 1
    path = records[0]
    assert [step.ordinal for step in path.steps] == [0, 1, 2]
    assert [step.span.source_path for step in path.steps] == [
        "frameworks/base/demo/Service.java",
        "frameworks/base/demo/Helper.java",
        "frameworks/base/demo/Store.java",
    ]
    assert path.steps[0].symbol_key == "java|method|demo.Service#entry(java.lang.String)"
    assert path.steps[1].symbol_key == ""
    assert path.steps[2].symbol_key == "java|method|demo.Store#write(java.lang.String)"
