from __future__ import annotations

from pathlib import Path

from collectors.interop.managed_native_scanner import scan_managed_native


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native/managed"
REVISION = "d" * 40


def native_facts(result):
    return [
        item
        for item in result.facts
        if item.fact_kind == "MANAGED_NATIVE_DECLARATION"
    ]


def test_java_native_methods_include_descriptors_overloads_and_inner_class() -> None:
    result = scan_managed_native(
        FIXTURE / "Demo.java",
        language="java",
        repository="frameworks/base",
        revision=REVISION,
    )
    identities = {item.logical_identity for item in native_facts(result)}

    assert identities == {
        "managed-native:java:com.example.Demo#sum(II)I",
        "managed-native:java:com.example.Demo#names([B)[Ljava/lang/String;",
        "managed-native:java:com.example.Demo#overloaded(I)I",
        "managed-native:java:com.example.Demo#overloaded(Ljava/lang/String;)I",
        "managed-native:java:com.example.Demo$Inner#ping(Ljava/lang/String;)V",
    }
    assert result.grammar_name == "managed-native-structural-java"
    assert len(result.grammar_fingerprint) == 64
    assert result.diagnostics == ()


def test_kotlin_external_functions_include_arrays_overloads_and_inner_class() -> None:
    result = scan_managed_native(
        FIXTURE / "Demo.kt",
        language="kotlin",
        repository="frameworks/base",
        revision=REVISION,
    )
    identities = {item.logical_identity for item in native_facts(result)}

    assert identities == {
        "managed-native:kotlin:com.example.KDemo#read([B)Ljava/lang/String;",
        "managed-native:kotlin:com.example.KDemo#overloaded(I)I",
        "managed-native:kotlin:com.example.KDemo#overloaded(Ljava/lang/String;)I",
        "managed-native:kotlin:com.example.KDemo$Inner#ping([Ljava/lang/String;)V",
    }
    assert result.grammar_name == "managed-native-structural-kotlin"
    assert result.diagnostics == ()


def test_scanner_ignores_non_native_methods(tmp_path: Path) -> None:
    source = tmp_path / "Plain.java"
    source.write_text(
        "package demo; class Plain { int run() { return 1; } }",
        encoding="utf-8",
    )

    result = scan_managed_native(
        source,
        language="java",
        repository="demo",
        revision=REVISION,
    )

    assert native_facts(result) == []


def test_scanner_rejects_unsupported_language(tmp_path: Path) -> None:
    source = tmp_path / "Demo.swift"
    source.write_text("class Demo {}", encoding="utf-8")

    try:
        scan_managed_native(source, "swift", "demo", REVISION)
    except ValueError as error:
        assert "java or kotlin" in str(error)
    else:
        raise AssertionError("unsupported language was accepted")
