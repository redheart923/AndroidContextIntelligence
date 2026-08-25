from __future__ import annotations

import json
from pathlib import Path

from collectors.facts.identity import fact_identity
from collectors.native.ctags_symbols import ctags_command, decode_ctags_record


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECORDS = PROJECT_ROOT / "tests/fixtures/native/ctags_records.jsonl"


def records() -> list[dict[str, object]]:
    return [json.loads(line) for line in RECORDS.read_text().splitlines()]


def test_ctags_command_is_deterministic_for_c_and_cpp(tmp_path: Path) -> None:
    root = tmp_path / "source"
    output = tmp_path / "tags.jsonl"

    cpp = ctags_command(root, "cpp", output)
    c = ctags_command(root, "c", output)

    assert cpp == [
        "ctags",
        "--output-format=json",
        "--fields=+nKSEs",
        "--extras=-F",
        "--languages=C++",
        "--recurse=yes",
        "--append=no",
        "-f",
        str(output),
        str(root),
    ]
    assert c[4] == "--languages=C"


def test_cpp_records_decode_namespace_type_and_method() -> None:
    namespace, type_record, method = records()[:3]

    namespace_fact = decode_ctags_record(namespace, "frameworks/native")
    type_fact = decode_ctags_record(type_record, "frameworks/native")
    method_fact = decode_ctags_record(method, "frameworks/native")

    assert namespace_fact[0].fact_kind == "CPP_NAMESPACE"
    assert namespace_fact[0].logical_identity == "cpp:namespace:android"
    assert type_fact[0].fact_kind == "CPP_TYPE"
    assert type_fact[0].logical_identity == "cpp:type:android::demo::Foo"
    assert method_fact[0].fact_kind == "CPP_METHOD"
    assert method_fact[0].logical_identity == (
        "cpp:method:android::demo::Foo::run(int value)"
    )
    assert method_fact[0].properties["return_type"] == "int"


def test_c_function_and_macro_have_distinct_native_kinds() -> None:
    macro, function = records()[-2:]

    macro_fact = decode_ctags_record(macro, "frameworks/native")[0]
    function_fact = decode_ctags_record(function, "frameworks/native")[0]

    assert macro_fact.fact_kind == "C_MACRO"
    assert macro_fact.logical_identity == "c:macro:DEMO_FLAG"
    assert function_fact.fact_kind == "C_FUNCTION"
    assert function_fact.logical_identity == "c:function:demo_start(int value)"


def test_cpp_overloads_have_different_logical_identities() -> None:
    integer, string = records()[3:5]

    integer_fact = decode_ctags_record(integer, "frameworks/native")[0]
    string_fact = decode_ctags_record(string, "frameworks/native")[0]

    assert integer_fact.logical_identity == "cpp:function:android::demo::parse(int value)"
    assert string_fact.logical_identity == (
        "cpp:function:android::demo::parse(const char * value)"
    )
    assert integer_fact.logical_identity != string_fact.logical_identity


def test_same_definition_in_two_repositories_retains_both_evidence_records() -> None:
    record = records()[3]

    platform = decode_ctags_record(record, "frameworks/native")[0]
    vendor = decode_ctags_record(record, "vendor/example/native")[0]

    assert platform.logical_identity == vendor.logical_identity
    assert platform.evidence.repository == "frameworks/native"
    assert vendor.evidence.repository == "vendor/example/native"
    assert fact_identity(platform) != fact_identity(vendor)


def test_unknown_ctags_kind_does_not_create_a_symbol() -> None:
    record = {
        "_type": "tag",
        "name": "value",
        "path": "frameworks/native/demo/Foo.cpp",
        "line": 1,
        "kind": "local",
        "language": "C++",
    }

    assert decode_ctags_record(record, "frameworks/native") == ()


def test_absolute_ctags_path_is_normalized_to_workspace_relative_path() -> None:
    record = dict(records()[3])
    record["path"] = "/home/ts/aosp/frameworks/native/demo/Foo.cpp"

    fact = decode_ctags_record(record, "frameworks/native")[0]

    assert fact.source_range.source_path == "frameworks/native/demo/Foo.cpp"
