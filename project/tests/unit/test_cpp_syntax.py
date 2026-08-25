from __future__ import annotations

from pathlib import Path

from collectors.facts.model import DiagnosticFact, RelationFact, SymbolFact
from collectors.native.cpp_syntax import parse_cpp_file


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/native/cpp"


def test_cpp_parser_extracts_namespace_type_method_include_and_inheritance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Derived.cpp"
    source.write_text(
        '''#include "demo/Foo.h"
namespace android::demo {
class Derived : public Foo {
public:
    int run(int value) { return value; }
};
}
''',
        encoding="utf-8",
    )

    result = parse_cpp_file(
        source,
        "cpp",
        "frameworks/native",
        "a" * 40,
    )

    symbols = [fact for fact in result.facts if isinstance(fact, SymbolFact)]
    relations = [fact for fact in result.facts if isinstance(fact, RelationFact)]
    assert {(fact.fact_kind, fact.logical_identity) for fact in symbols} >= {
        ("CPP_NAMESPACE", "cpp:namespace:android::demo"),
        ("CPP_TYPE", "cpp:type:android::demo::Derived"),
        ("CPP_METHOD", "cpp:method:android::demo::Derived::run(int value)"),
    }
    assert {(fact.fact_kind, fact.to_identity) for fact in relations} >= {
        ("INCLUDES", "demo/Foo.h"),
        ("EXTENDS", "cpp:type:android::demo::Foo"),
    }
    assert result.diagnostics == ()
    assert result.grammar_name == "tree-sitter-cpp"
    assert len(result.grammar_fingerprint) == 64


def test_c_parser_extracts_function_and_include(tmp_path: Path) -> None:
    source = tmp_path / "demo.c"
    source.write_text(
        '#include <stdint.h>\nint start(int value) { return value; }\n',
        encoding="utf-8",
    )

    result = parse_cpp_file(source, "c", "system/demo", None)

    assert any(
        isinstance(fact, SymbolFact)
        and fact.fact_kind == "C_FUNCTION"
        and fact.logical_identity == "c:function:start(int value)"
        for fact in result.facts
    )
    assert any(
        isinstance(fact, RelationFact)
        and fact.fact_kind == "INCLUDES"
        and fact.to_identity == "stdint.h"
        for fact in result.facts
    )
    assert result.grammar_name == "tree-sitter-c"


def test_parse_error_is_reported_without_fabricated_enclosing_relation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "broken.cpp"
    source.write_text("class Broken : public {\n", encoding="utf-8")

    result = parse_cpp_file(source, "cpp", "vendor/demo", None)

    assert any(
        isinstance(item, DiagnosticFact)
        and item.reason_code == "tree_sitter_parse_error"
        for item in result.diagnostics
    )
    assert not any(
        isinstance(item, RelationFact) and item.fact_kind == "EXTENDS"
        for item in result.facts
    )


def test_cpp_parser_output_and_grammar_fingerprint_are_deterministic() -> None:
    path = FIXTURES / "src/Foo.cpp"

    first = parse_cpp_file(path, "cpp", "frameworks/native", "b" * 40)
    second = parse_cpp_file(path, "cpp", "frameworks/native", "b" * 40)

    assert first == second
    symbols = [fact for fact in first.facts if isinstance(fact, SymbolFact)]
    identities = {(fact.fact_kind, fact.logical_identity) for fact in symbols}
    assert (
        "CPP_METHOD",
        "cpp:method:android::demo::Foo::run(int value)",
    ) in identities
    assert (
        "CPP_FUNCTION",
        "cpp:function:android::demo::parse(int value)",
    ) in identities
