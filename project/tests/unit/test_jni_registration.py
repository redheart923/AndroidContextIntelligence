from __future__ import annotations

from pathlib import Path

from collectors.facts.model import CandidateFact, SymbolFact
from collectors.interop.jni_cpp_scanner import (
    scan_jni_cpp_file,
    scan_native_registrations,
)
from collectors.interop.rust_ffi_scanner import scan_rust_exports
from collectors.native.rust_syntax import parse_rust_file


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/native"
REVISION = "d" * 40


def test_jni_method_arrays_and_three_registration_apis_are_structured() -> None:
    parsed = scan_jni_cpp_file(
        FIXTURES / "cpp/src/jni_registration.cpp",
        repository="frameworks/base",
        revision=REVISION,
    )
    registrations = scan_native_registrations((parsed,))
    explicit = [
        item
        for item in registrations
        if isinstance(item, CandidateFact)
        and item.candidate_kind == "explicit_jni_registration"
    ]

    assert {
        (item.properties["class_name"], item.properties["method_name"], item.properties["descriptor"])
        for item in explicit
    } == {
        ("com.example.Demo", "sum", "(II)I"),
        ("com.example.Demo$Inner", "ping", "(Ljava/lang/String;)V"),
    }
    assert {item.properties["registration_api"] for item in explicit} == {
        "jniRegisterNativeMethods",
        "AndroidRuntime::registerNativeMethods",
        "JNIEnv::RegisterNatives",
    }
    assert all(item.properties["native_symbol"] for item in explicit)


def test_cpp_scanner_emits_native_functions_used_by_registration() -> None:
    parsed = scan_jni_cpp_file(
        FIXTURES / "cpp/src/jni_registration.cpp",
        "frameworks/base",
        REVISION,
    )
    exports = [
        item
        for item in parsed.facts
        if isinstance(item, SymbolFact) and item.fact_kind == "NATIVE_FUNCTION"
    ]

    assert {item.properties["export_name"] for item in exports} >= {
        "nativeSum",
        "nativePing",
    }


def test_rust_export_scanner_preserves_export_names() -> None:
    rust = parse_rust_file(
        FIXTURES / "rust/src/lib.rs",
        {"crate_name": "demo", "known_cfg": ()},
        "system/demo",
        REVISION,
    )

    exports = scan_rust_exports((rust,))

    assert {
        item.properties["export_name"]
        for item in exports
        if isinstance(item, SymbolFact)
    } >= {"native_start", "demo_alias"}
