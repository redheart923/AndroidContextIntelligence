from __future__ import annotations

import pytest

from collectors.build.blueprint_lexer import BlueprintLexError, lex_blueprint
from collectors.build.blueprint_parser import BlueprintSyntaxError, parse_blueprint


def test_lexer_skips_comments_and_decodes_escaped_strings() -> None:
    tokens = lex_blueprint(
        '''// line comment
        /* block
           comment */
        value = "line\\n\\\"quoted\\\""
        ''',
        "demo/Android.bp",
    )

    assert [(token.kind, token.value) for token in tokens] == [
        ("IDENT", "value"),
        ("EQUAL", "="),
        ("STRING", 'line\n"quoted"'),
        ("EOF", ""),
    ]


def test_parser_preserves_nested_lists_maps_and_trailing_commas() -> None:
    document = parse_blueprint(
        '''cc_library {
            name: "demo",
            flags: ["a", "b",],
            arch: { arm64: { srcs: ["arm.cpp"], }, },
        }
        ''',
        "frameworks/native/Android.bp",
    )
    module = document.modules[0]

    assert module.module_type == "cc_library"
    assert module.property("name").value.to_plain() == "demo"
    assert module.property("flags").value.to_plain() == ["a", "b"]
    assert module.property("arch").value.to_plain() == {
        "arm64": {"srcs": ["arm.cpp"]}
    }


def test_parser_records_variable_assignment_append_and_concatenation() -> None:
    document = parse_blueprint(
        '''srcs = ["base.cpp"]
        srcs += ["extra.cpp"]
        cc_library { name: "demo", srcs: srcs + ["local.cpp"] }
        ''',
        "frameworks/native/Android.bp",
    )

    assert [(item.name, item.operator) for item in document.assignments] == [
        ("srcs", "="),
        ("srcs", "+="),
    ]
    expression = document.modules[0].property("srcs").value
    assert expression.kind == "concat"
    assert expression.items[0].kind == "identifier"
    assert expression.items[0].value == "srcs"
    assert expression.items[1].to_plain() == ["local.cpp"]


def test_parser_preserves_call_expression_for_later_conditional_resolution() -> None:
    document = parse_blueprint(
        '''cc_library {
            name: "demo",
            shared_libs: select(soong_config_variable("ns", "key"), {
                "enabled": ["libenabled"],
                default: [],
            }),
        }
        ''',
        "vendor/demo/Android.bp",
    )
    expression = document.modules[0].property("shared_libs").value

    assert expression.kind == "call"
    assert expression.value == "select"
    assert expression.items[0].kind == "call"
    assert expression.items[1].kind == "map"


def test_parser_reports_exact_one_based_source_ranges() -> None:
    document = parse_blueprint(
        '''value = ["x"]

cc_library {
    name: "demo",
}
''',
        "demo/Android.bp",
    )
    module = document.modules[0]

    assert (
        module.span.line_start,
        module.span.column_start,
        module.span.line_end,
        module.span.column_end,
    ) == (3, 1, 5, 2)
    assert (
        module.property("name").span.line_start,
        module.property("name").span.column_start,
    ) == (4, 5)


def test_lexer_and_parser_fail_closed_on_unterminated_input() -> None:
    with pytest.raises(BlueprintLexError, match="unterminated string"):
        lex_blueprint('name = "broken', "demo/Android.bp")
    with pytest.raises(BlueprintSyntaxError, match="expected RBRACE"):
        parse_blueprint(
            'cc_library { name: "broken"',
            "demo/Android.bp",
        )
