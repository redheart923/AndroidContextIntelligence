from __future__ import annotations

import pytest

from collectors.permission.lexical import LexicalError, iter_permission_constructs


def test_multiline_constructs_preserve_exact_ranges_and_ignore_decoys() -> None:
    text = '''package example;
class Service {
    String decoy = "enforceCallingPermission(\"android.permission.FAKE\")";
    @RequiresPermission(
        allOf = {
            Manifest.permission.CAMERA
        })
    void open() {
        // checkPermission("android.permission.FAKE");
        enforceCallingPermission(
            "android.permission.CAMERA",
            messageFor("nested ) argument")
        );
    }
}
'''

    constructs = iter_permission_constructs(text)

    assert [(item.name, item.line_start, item.line_end) for item in constructs] == [
        ("RequiresPermission", 4, 7),
        ("enforceCallingPermission", 10, 13),
    ]
    assert "allOf" in constructs[0].argument_expression
    assert "messageFor" in constructs[1].argument_expression


def test_kotlin_comments_characters_and_triple_strings_are_ignored() -> None:
    text = '''class Service {
    val decoy = """checkPermission("android.permission.FAKE")"""
    val closing = ')'
    /* enforcePermission("android.permission.FAKE") */
    fun open() {
        checkSelfPermission(
            android.Manifest.permission.CAMERA
        )
    }
}
'''

    constructs = iter_permission_constructs(text)

    assert [(item.name, item.line_start, item.line_end) for item in constructs] == [
        ("checkSelfPermission", 6, 8)
    ]


def test_unterminated_recognized_construct_reports_start_line() -> None:
    with pytest.raises(LexicalError) as raised:
        iter_permission_constructs(
            "class Service {\n  void open() {\n    enforcePermission(\n"
        )

    assert raised.value.line_start == 3
