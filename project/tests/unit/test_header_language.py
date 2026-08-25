from __future__ import annotations

from pathlib import Path

from collectors.native.header_language import resolve_header_language


def test_header_language_uses_unique_compile_context() -> None:
    path = Path("frameworks/native/include/demo/Foo.h")

    result = resolve_header_language(
        path,
        {path.as_posix(): ("cpp",)},
        {},
    )

    assert result == "cpp"


def test_header_language_uses_unique_soong_owner_context() -> None:
    path = Path("system/demo/include/demo.h")

    result = resolve_header_language(
        path,
        {},
        {path.as_posix(): ("c", "c")},
    )

    assert result == "c"


def test_header_language_rejects_conflicting_contexts() -> None:
    path = Path("vendor/demo/include/shared.h")

    result = resolve_header_language(
        path,
        {path.as_posix(): ("c",)},
        {path.as_posix(): ("cpp",)},
    )

    assert result is None


def test_header_language_without_context_is_ambiguous() -> None:
    assert resolve_header_language(Path("demo.h"), {}, {}) is None
