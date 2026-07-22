from __future__ import annotations

import sqlite3
from pathlib import Path

from collectors.permission.model import PermissionFactKind
from collectors.permission.source_permission_scanner import (
    MethodRange,
    find_containing_method,
    load_method_ranges,
    scan_permission_source,
)


def line_of(text: str, fragment: str) -> int:
    return next(
        index
        for index, line in enumerate(text.splitlines(), start=1)
        if fragment in line
    )


def scan(
    tmp_path: Path,
    text: str,
    methods: tuple[MethodRange, ...],
    language: str = "java",
):
    suffix = ".kt" if language == "kotlin" else ".java"
    path = tmp_path / f"Service{suffix}"
    path.write_text(text, encoding="utf-8")
    return scan_permission_source(
        path,
        repository="frameworks/base",
        source_path=f"frameworks/base/Service{suffix}",
        source_revision="0123456789abcdef0123456789abcdef01234567",
        language=language,
        methods=methods,
    )


def test_find_containing_method_uses_smallest_complete_range() -> None:
    methods = (
        MethodRange("JAVA_METHOD:A#outer()", 10, 40),
        MethodRange("JAVA_METHOD:A#one()", 10, 20),
        MethodRange("JAVA_METHOD:A#two()", 30, 40),
        MethodRange("JAVA_METHOD:A#missingEnd()", 50, None),
    )

    assert find_containing_method(methods, 15, 15).node_id.endswith("one()")
    assert find_containing_method(methods, 25, 25).node_id.endswith("outer()")
    assert find_containing_method(methods, 31, 32).node_id.endswith("two()")
    assert find_containing_method(methods, 50, 50) is None
    assert find_containing_method(
        (methods[1], methods[2]),
        25,
        25,
    ) is None


def test_load_method_ranges_rejects_incomplete_rows() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE node(node_id TEXT, node_type TEXT, source_path TEXT, "
        "line_start INTEGER, line_end INTEGER)"
    )
    connection.executemany(
        "INSERT INTO node VALUES(?,?,?,?,?)",
        [
            ("JAVA_METHOD:A#one()", "JAVA_METHOD", "A.java", 10, 20),
            ("JAVA_METHOD:A#bad()", "JAVA_METHOD", "A.java", 30, None),
            ("JAVA_CLASS:A", "JAVA_CLASS", "A.java", 1, 40),
        ],
    )

    assert load_method_ranges(connection, "A.java") == (
        MethodRange("JAVA_METHOD:A#one()", 10, 20),
    )


def test_requires_permission_value_all_of_any_of_and_conditional(
    tmp_path: Path,
) -> None:
    text = '''import android.Manifest;
class Service {
  @RequiresPermission(Manifest.permission.CAMERA)
  void single() {}
  @RequiresPermission(allOf = {Manifest.permission.CAMERA,
      Manifest.permission.RECORD_AUDIO}, conditional = true)
  void all() {}
  @RequiresPermission(anyOf = {Manifest.permission.CAMERA,
      Manifest.permission.POST_NOTIFICATIONS})
  void any() {}
}
'''
    methods = tuple(
        MethodRange(f"JAVA_METHOD:Service#{name}()", line_of(text, declaration), line_of(text, declaration))
        for name, declaration in (
            ("single", "void single"),
            ("all", "void all"),
            ("any", "void any"),
        )
    )

    outcome = scan(tmp_path, text, methods)

    assert len(outcome.facts) == 5
    by_owner = {}
    for fact in outcome.facts:
        by_owner.setdefault(fact.owner_node_id, []).append(fact)
        assert fact.kind is PermissionFactKind.REQUIRES_PERMISSION
    assert [fact.properties["requirement_mode"] for fact in by_owner[methods[0].node_id]] == ["single"]
    assert {fact.permission_name for fact in by_owner[methods[1].node_id]} == {
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
    }
    assert all(fact.properties["requirement_mode"] == "all_of" for fact in by_owner[methods[1].node_id])
    assert all(fact.properties["conditional"] is True for fact in by_owner[methods[1].node_id])
    assert all(fact.properties["requirement_mode"] == "any_of" for fact in by_owner[methods[2].node_id])


def test_checks_and_throwing_enforcement_are_different_semantics(
    tmp_path: Path,
) -> None:
    text = '''import android.Manifest;
class Service {
  void run() {
    checkCallingPermission(Manifest.permission.CAMERA);
    enforceCallingPermission(Manifest.permission.RECORD_AUDIO, "message");
    unknownPermissionWrapper("android.permission.POST_NOTIFICATIONS");
  }
}
'''
    methods = (
        MethodRange(
            "JAVA_METHOD:Service#run()",
            line_of(text, "void run"),
            line_of(text, "  }"),
        ),
    )

    outcome = scan(tmp_path, text, methods)

    assert [(fact.kind, fact.permission_name) for fact in outcome.facts] == [
        (PermissionFactKind.CHECKS_PERMISSION, "android.permission.CAMERA"),
        (PermissionFactKind.ENFORCES_PERMISSION, "android.permission.RECORD_AUDIO"),
    ]
    assert {fact.properties["api_name"] for fact in outcome.facts} == {
        "checkCallingPermission",
        "enforceCallingPermission",
    }


def test_kotlin_source_uses_the_same_bounded_semantics(tmp_path: Path) -> None:
    text = '''import android.Manifest
class Service {
  fun run() {
    checkSelfPermission(Manifest.permission.CAMERA)
    enforcePermission("android.permission.RECORD_AUDIO", 1, 2, "message")
  }
}
'''
    methods = (
        MethodRange(
            "KOTLIN_METHOD:Service#run",
            line_of(text, "fun run"),
            line_of(text, "  }"),
        ),
    )

    outcome = scan(tmp_path, text, methods, language="kotlin")

    assert [fact.kind for fact in outcome.facts] == [
        PermissionFactKind.CHECKS_PERMISSION,
        PermissionFactKind.ENFORCES_PERMISSION,
    ]


def test_kotlin_requires_permission_array_is_resolved(tmp_path: Path) -> None:
    text = '''import android.Manifest
class Service {
  @RequiresPermission(anyOf = [
      Manifest.permission.CAMERA,
      Manifest.permission.RECORD_AUDIO
  ])
  fun read() {}
}
'''
    method = MethodRange(
        "KOTLIN_METHOD:Service#read",
        line_of(text, "fun read"),
        line_of(text, "fun read"),
    )

    outcome = scan(tmp_path, text, (method,), language="kotlin")

    assert {fact.permission_name for fact in outcome.facts} == {
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
    }
    assert all(
        fact.properties["requirement_mode"] == "any_of"
        for fact in outcome.facts
    )


def test_unresolved_expression_and_owner_are_separate_diagnostics(
    tmp_path: Path,
) -> None:
    text = '''class Service {
  void run() {
    checkPermission(permissionProvider());
  }
}
enforceCallingPermission("android.permission.CAMERA", "outside");
'''
    methods = (
        MethodRange(
            "JAVA_METHOD:Service#run()",
            line_of(text, "void run"),
            line_of(text, "  }"),
        ),
    )

    outcome = scan(tmp_path, text, methods)

    assert outcome.facts == ()
    assert {item.category for item in outcome.diagnostics} == {
        "unresolved_permission_expressions",
        "unresolved_method_owners",
    }


def test_nested_requires_permission_form_is_explicitly_unsupported(
    tmp_path: Path,
) -> None:
    text = '''class Service {
  @RequiresPermission.Read(RequiresPermission("android.permission.CAMERA"))
  void read() {}
}
'''
    methods = (
        MethodRange(
            "JAVA_METHOD:Service#read()",
            line_of(text, "void read"),
            line_of(text, "void read"),
        ),
    )

    outcome = scan(tmp_path, text, methods)

    assert outcome.facts == ()
    assert [(item.category, item.reason_code) for item in outcome.diagnostics] == [
        ("unsupported_constructs", "nested_requires_permission")
    ]


def test_recognized_api_method_declaration_is_not_a_call(tmp_path: Path) -> None:
    text = '''class Service {
  int checkPermission(String permission) {
    return 0;
  }
}
'''
    method = MethodRange(
        "JAVA_METHOD:Service#checkPermission(String permission)",
        line_of(text, "int checkPermission"),
        line_of(text, "  }"),
    )

    outcome = scan(tmp_path, text, (method,))

    assert outcome.facts == ()
    assert outcome.diagnostics == ()
