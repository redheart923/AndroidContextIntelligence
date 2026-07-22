from __future__ import annotations

import pytest

from collectors.permission.resolver import (
    SourceBindings,
    resolve_permission_expression,
)


@pytest.mark.parametrize(
    ("expression", "expected", "kind"),
    [
        ('"android.permission.CAMERA"', ("android.permission.CAMERA",), "literal"),
        (
            "android.Manifest.permission.CAMERA",
            ("android.permission.CAMERA",),
            "manifest_constant",
        ),
        (
            "Manifest.permission.RECORD_AUDIO",
            ("android.permission.RECORD_AUDIO",),
            "manifest_constant",
        ),
        ("POST_NOTIFICATIONS", ("android.permission.POST_NOTIFICATIONS",), "static_import"),
        ("LOCAL_PERMISSION", ("android.permission.CAMERA",), "alias"),
    ],
)
def test_java_literals_imports_static_imports_and_aliases(
    expression: str,
    expected: tuple[str, ...],
    kind: str,
) -> None:
    bindings = SourceBindings.from_text(
        '''package example;
import android.Manifest;
import static android.Manifest.permission.POST_NOTIFICATIONS;
class Service {
  static final String BASE_PERMISSION = Manifest.permission.CAMERA;
  static final String LOCAL_PERMISSION = BASE_PERMISSION;
}
''',
        "java",
    )

    resolution = resolve_permission_expression(expression, bindings)

    assert resolution.values == expected
    assert resolution.kind == kind
    assert resolution.reason_code is None


def test_kotlin_const_alias_and_explicit_collection() -> None:
    bindings = SourceBindings.from_text(
        '''package example
import android.Manifest.permission.CAMERA
const val LOCAL = CAMERA
const val AUDIO = "android.permission.RECORD_AUDIO"
''',
        "kotlin",
    )

    alias = resolve_permission_expression("LOCAL", bindings)
    collection = resolve_permission_expression("arrayOf(LOCAL, AUDIO)", bindings)

    assert alias.values == ("android.permission.CAMERA",)
    assert alias.kind == "alias"
    assert collection.values == (
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
    )
    assert collection.kind == "collection"


def test_java_array_resolves_only_explicit_elements() -> None:
    bindings = SourceBindings.from_text("", "java")

    resolution = resolve_permission_expression(
        'new String[] {"android.permission.CAMERA", '
        'android.Manifest.permission.RECORD_AUDIO}',
        bindings,
    )

    assert resolution.values == (
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
    )
    assert resolution.kind == "collection"


def test_alias_cycle_is_reported() -> None:
    bindings = SourceBindings.from_text(
        "static final String A = B;\nstatic final String B = A;\n",
        "java",
    )

    resolution = resolve_permission_expression("A", bindings)

    assert resolution.values == ()
    assert resolution.reason_code == "alias_cycle"


def test_ambiguous_manifest_import_is_not_guessed() -> None:
    bindings = SourceBindings.from_text(
        "import android.Manifest;\nimport example.Manifest;\n",
        "java",
    )

    resolution = resolve_permission_expression(
        "Manifest.permission.CAMERA",
        bindings,
    )

    assert resolution.values == ()
    assert resolution.reason_code == "ambiguous_import"


@pytest.mark.parametrize(
    "expression",
    [
        "UNKNOWN_PERMISSION",
        '"android.permission." + suffix',
        "permissionProvider()",
    ],
)
def test_unknown_or_computed_expression_is_not_guessed(expression: str) -> None:
    resolution = resolve_permission_expression(
        expression,
        SourceBindings.from_text("", "java"),
    )

    assert resolution.values == ()
    assert resolution.reason_code == "unsupported_expression"
