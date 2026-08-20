from __future__ import annotations

import json
import subprocess
from pathlib import Path

from workspace.codeql_runner import query_result_key, run_queries


CSV = """schema_version,record_kind,language,package_name,declaring_type,callable_kind,callable_name,erased_parameters,return_type,repository_path,source_path,start_line,start_column,end_line,end_column,caller_symbol_key,callee_symbol_key,dispatch_kind,relation_kind,candidate_count,expression_text,unresolved_reason
1,definition,java,demo,demo.A,method,run,,void,demo,demo/A.java,1,1,1,5,demo.A#run(),,,,0,run,
"""

SARIF = json.dumps(
    {
        "runs": [
            {
                "results": [
                    {
                        "message": {
                            "text": "ACI1;scenario=binder_argument_to_sensitive_sink;"
                            "entry=java|method|demo.A#run();source_parameter_index=0;"
                            "source_repository=demo;source_path=demo/A.java;"
                            "sink_owner=java|method|demo.A#run();"
                            "sink_callable=demo.Store.write;sink_repository=demo;"
                            "sink_path=demo/A.java"
                        },
                        "codeFlows": [
                            {
                                "threadFlows": [
                                    {
                                        "locations": [
                                            {
                                                "location": {
                                                    "physicalLocation": {
                                                        "artifactLocation": {
                                                            "uri": "file:///src/demo/A.java"
                                                        },
                                                        "region": {
                                                            "startLine": 1,
                                                            "startColumn": 1,
                                                            "endColumn": 4,
                                                        },
                                                    },
                                                    "message": {"text": "value"},
                                                }
                                            },
                                            {
                                                "location": {
                                                    "physicalLocation": {
                                                        "artifactLocation": {
                                                            "uri": "file:///src/demo/A.java"
                                                        },
                                                        "region": {
                                                            "startLine": 2,
                                                            "startColumn": 1,
                                                            "endColumn": 4,
                                                        },
                                                    },
                                                    "message": {"text": "value"},
                                                }
                                            },
                                        ]
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        ]
    }
)


class FakeCodeQL:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        assert capture_output is True
        assert text is True
        self.commands.append(tuple(command))
        if command[1:3] == ["version", "--format=json"]:
            return subprocess.CompletedProcess(command, 0, '{"version":"2.26.3"}', "")
        if command[1:3] == ["resolve", "languages"]:
            return subprocess.CompletedProcess(
                command,
                0,
                '{"java":{"extractorPack":"codeql/java-all"}}',
                "",
            )
        output = Path(command[command.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        if command[1:3] == ["query", "run"]:
            output.write_bytes(b"fixture-bqrs")
        elif command[1:3] == ["bqrs", "decode"]:
            output.write_text(CSV, encoding="utf-8")
        elif command[1:3] == ["bqrs", "interpret"]:
            output.write_text(SARIF, encoding="utf-8")
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, "", "")


def fixture_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    database = tmp_path / "database"
    database.mkdir()
    (database / "codeql-database.yml").write_text("primaryLanguage: java\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "database_fingerprint": "d" * 64,
                "repositories": [{"path": "demo"}],
            }
        ),
        encoding="utf-8",
    )
    pack = tmp_path / "pack"
    (pack / "queries").mkdir(parents=True)
    (pack / "queries/CallSites.ql").write_text("/** @kind table */\nselect 1\n", encoding="utf-8")
    (pack / "codeql-pack.lock.yml").write_text("lockVersion: 1\n", encoding="utf-8")
    codeql = tmp_path / "codeql"
    codeql.write_text("fixture", encoding="utf-8")
    return database, pack, tmp_path / "raw/codeql", codeql


def test_query_result_key_includes_database_and_pack_lock() -> None:
    first = query_result_key("db-a", "lock-a", "CallSites", "1", {})
    assert first != query_result_key("db-b", "lock-a", "CallSites", "1", {})
    assert first != query_result_key("db-a", "lock-b", "CallSites", "1", {})
    assert first != query_result_key("db-a", "lock-a", "CallSites", "2", {})


def test_run_queries_writes_normalized_manifest_and_reuses_verified_cache(tmp_path: Path) -> None:
    database, pack, output, codeql = fixture_tree(tmp_path)
    fake = FakeCodeQL()

    first = run_queries(database, pack, output, codeql, runner=fake)
    second = run_queries(database, pack, output, codeql, runner=fake)

    assert first.queries[0].cache_status == "miss"
    assert second.queries[0].cache_status == "hit"
    assert first.queries[0].row_count == 1
    assert first.queries[0].raw_sha256
    assert first.queries[0].normalized_sha256
    assert first.codeql_version == "2.26.3"
    assert first.extractor_version.startswith("resolve-languages:")
    assert (output / "query-run-manifest.json").is_file()
    assert (output / "normalized/CallSites.jsonl").is_file()
    assert sum(command[1:3] == ("query", "run") for command in fake.commands) == 1


def test_tampered_cache_is_not_reused(tmp_path: Path) -> None:
    database, pack, output, codeql = fixture_tree(tmp_path)
    fake = FakeCodeQL()
    first = run_queries(database, pack, output, codeql, runner=fake)
    Path(first.queries[0].normalized_path).write_text("tampered\n", encoding="utf-8")

    second = run_queries(database, pack, output, codeql, runner=fake)

    assert second.queries[0].cache_status == "miss"
    assert sum(command[1:3] == ("query", "run") for command in fake.commands) == 2


def test_query_source_change_invalidates_result_cache(tmp_path: Path) -> None:
    database, pack, output, codeql = fixture_tree(tmp_path)
    fake = FakeCodeQL()
    first = run_queries(database, pack, output, codeql, runner=fake)
    (pack / "queries/CallSites.ql").write_text(
        "/** @kind table */\nselect 2\n", encoding="utf-8"
    )

    second = run_queries(database, pack, output, codeql, runner=fake)

    assert first.queries[0].cache_key != second.queries[0].cache_key
    assert second.queries[0].cache_status == "miss"


def test_path_query_is_interpreted_as_sarif_and_preserves_thread_flow(
    tmp_path: Path,
) -> None:
    database, pack, output, codeql = fixture_tree(tmp_path)
    (pack / "queries/CallSites.ql").unlink()
    (pack / "queries/SystemServiceDataflow.ql").write_text(
        "/** @kind path-problem */\nselect 1\n", encoding="utf-8"
    )
    fake = FakeCodeQL()

    manifest = run_queries(database, pack, output, codeql, runner=fake)

    assert manifest.queries[0].row_count == 1
    assert manifest.queries[0].decoded_format == "sarifv2.1.0"
    assert Path(manifest.queries[0].decoded_path).suffix == ".sarif"
    assert any(command[1:3] == ("bqrs", "interpret") for command in fake.commands)
