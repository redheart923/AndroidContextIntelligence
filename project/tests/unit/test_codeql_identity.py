from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from collectors.codeql.identity import reconcile_definition
from collectors.codeql.model import DefinitionRecord, SourceSpan


def graph_db(tmp_path: Path) -> Path:
    path = tmp_path / "graph.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE node (
              node_id TEXT PRIMARY KEY,
              node_type TEXT NOT NULL,
              qualified_name TEXT,
              source_path TEXT,
              line_start INTEGER,
              line_end INTEGER,
              properties_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
    return path


def seed_method(
    database: Path,
    node_id: str,
    qualified_name: str,
    *,
    node_type: str = "JAVA_METHOD",
    source_path: str = "frameworks/base/demo/A.java",
    line_start: int = 10,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO node(node_id,node_type,qualified_name,source_path,line_start,line_end) "
            "VALUES(?,?,?,?,?,?)",
            (node_id, node_type, qualified_name, source_path, line_start, line_start + 2),
        )


def definition(
    *,
    language: str = "java",
    declaring_type: str = "demo.A",
    name: str = "run",
    parameters: tuple[str, ...] = ("java.lang.String",),
    source_path: str = "frameworks/base/demo/A.java",
    line_start: int = 10,
) -> DefinitionRecord:
    return DefinitionRecord(
        language=language,
        package_name="demo",
        declaring_type=declaring_type,
        callable_kind="method",
        callable_name=name,
        erased_parameters=parameters,
        return_type="void",
        symbol_key=f"{declaring_type}#{name}({','.join(parameters)})",
        span=SourceSpan("frameworks/base", source_path, line_start, 1, line_start + 2, 1),
        query_id="CallSites",
        query_version="1",
        database_fingerprint="d" * 64,
    )


def test_erased_parameter_key_resolves_overload_uniquely(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    seed_method(database, "JAVA_METHOD:string", "demo.A#run(String value)")
    seed_method(database, "JAVA_METHOD:int", "demo.A#run(int value)", line_start=30)

    result = reconcile_definition(database, definition())

    assert result.status == "unique"
    assert result.logical_method_ids == ("JAVA_METHOD:string",)


def test_constructor_nested_type_and_kotlin_extension_are_supported(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    seed_method(
        database,
        "JAVA_METHOD:ctor",
        "demo.Outer.Inner#Inner(java.lang.String value)",
        source_path="frameworks/base/demo/Outer.java",
    )
    seed_method(
        database,
        "KOTLIN_METHOD:extension",
        "demo.ExtensionsKt#decorate(String receiver, String suffix)",
        node_type="KOTLIN_METHOD",
        source_path="packages/modules/Demo/Extensions.kt",
    )

    constructor = reconcile_definition(
        database,
        replace(
            definition(
                declaring_type="demo.Outer.Inner",
                name="Inner",
                source_path="frameworks/base/demo/Outer.java",
            ),
            callable_kind="constructor",
        ),
    )
    extension = reconcile_definition(
        database,
        definition(
            language="kotlin",
            declaring_type="demo.ExtensionsKt",
            name="decorate",
            parameters=("java.lang.String", "java.lang.String"),
            source_path="packages/modules/Demo/Extensions.kt",
        ),
    )

    assert constructor.logical_method_ids == ("JAVA_METHOD:ctor",)
    assert extension.logical_method_ids == ("KOTLIN_METHOD:extension",)


def test_annotations_generics_varargs_and_kotlin_parameter_names_are_erased(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    seed_method(
        database,
        "JAVA_METHOD:generic",
        "demo.A#run(@NonNull final java.util.List<String> values, String... rest)",
    )
    seed_method(
        database,
        "KOTLIN_METHOD:named",
        "demo.ExtensionsKt#decorate(receiver: String, suffix: String)",
        node_type="KOTLIN_METHOD",
        source_path="packages/modules/Demo/Extensions.kt",
    )

    generic = reconcile_definition(
        database,
        definition(parameters=("java.util.List", "java.lang.String[]")),
    )
    named = reconcile_definition(
        database,
        definition(
            language="kotlin",
            declaring_type="demo.ExtensionsKt",
            name="decorate",
            parameters=("java.lang.String", "java.lang.String"),
            source_path="packages/modules/Demo/Extensions.kt",
        ),
    )

    assert generic.logical_method_ids == ("JAVA_METHOD:generic",)
    assert named.logical_method_ids == ("KOTLIN_METHOD:named",)


def test_duplicate_logical_definitions_remain_ambiguous(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    seed_method(database, "JAVA_METHOD:first", "demo.A#run(String value)")
    seed_method(database, "JAVA_METHOD:second", "demo.A#run(java.lang.String arg)")

    result = reconcile_definition(database, definition())

    assert result.status == "ambiguous"
    assert result.logical_method_ids == ("JAVA_METHOD:first", "JAVA_METHOD:second")


def test_source_evidence_can_disambiguate_compatible_candidates(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    seed_method(database, "JAVA_METHOD:source", "demo.A#run(String value)")
    seed_method(
        database,
        "JAVA_METHOD:copy",
        "demo.A#run(String value)",
        source_path="vendor/demo/A.java",
    )

    result = reconcile_definition(database, definition())

    assert result.status == "unique"
    assert result.logical_method_ids == ("JAVA_METHOD:source",)


def test_unmatched_never_uses_name_only_fallback(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    seed_method(database, "JAVA_METHOD:other", "other.B#run(String value)")

    result = reconcile_definition(database, definition())

    assert result.status == "unmatched"
    assert result.logical_method_ids == ()


def test_lambda_and_anonymous_callables_are_synthetic(tmp_path: Path) -> None:
    database = graph_db(tmp_path)
    lambda_result = reconcile_definition(
        database,
        definition(declaring_type="demo.A", name="lambda$run$0", parameters=()),
    )
    anonymous_result = reconcile_definition(
        database,
        definition(declaring_type="demo.<anonymous>@A.java:20:3", name="run", parameters=()),
    )

    assert lambda_result.status == "synthetic"
    assert anonymous_result.status == "synthetic"
    assert lambda_result.logical_method_ids == ()
