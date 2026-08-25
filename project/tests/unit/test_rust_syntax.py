from __future__ import annotations

from pathlib import Path

from collectors.facts.model import CandidateFact, DiagnosticFact, RelationFact, SymbolFact
from collectors.native.rust_syntax import parse_rust_file


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native/rust/src/lib.rs"


def parse_fixture():
    return parse_rust_file(
        FIXTURE,
        {"crate_name": "demo", "known_cfg": ()},
        "system/demo",
        "c" * 40,
    )


def test_rust_parser_extracts_crate_module_type_trait_and_import() -> None:
    result = parse_fixture()
    symbols = {
        (fact.fact_kind, fact.logical_identity)
        for fact in result.facts
        if isinstance(fact, SymbolFact)
    }

    assert ("RUST_CRATE", "rust:crate:demo") in symbols
    assert ("RUST_MODULE", "rust:module:demo::bridge") in symbols
    assert ("RUST_TYPE", "rust:type:demo::bridge::Engine") in symbols
    assert ("RUST_TRAIT", "rust:trait:demo::bridge::Runner") in symbols
    assert any(
        isinstance(fact, RelationFact)
        and fact.fact_kind == "IMPORTS_SYMBOL"
        and fact.to_identity == "crate::model::Device"
        for fact in result.facts
    )


def test_rust_parser_extracts_trait_and_inherent_impl_relations() -> None:
    result = parse_fixture()
    relations = [fact for fact in result.facts if isinstance(fact, RelationFact)]

    assert any(
        fact.fact_kind == "IMPLEMENTS_TRAIT"
        and fact.from_identity == "rust:type:demo::bridge::Engine"
        and fact.to_identity == "rust:trait:demo::bridge::Runner"
        for fact in relations
    )
    assert any(
        fact.fact_kind == "IMPLEMENTS_TYPE"
        and fact.to_identity == "rust:type:demo::bridge::Engine"
        for fact in relations
    )
    assert any(
        isinstance(fact, SymbolFact)
        and fact.fact_kind == "RUST_FUNCTION"
        and fact.logical_identity.endswith("Engine::new()")
        for fact in result.facts
    )


def test_rust_parser_extracts_export_name_no_mangle_and_link_name() -> None:
    result = parse_fixture()
    relations = [fact for fact in result.facts if isinstance(fact, RelationFact)]

    assert any(
        fact.fact_kind == "EXPORTS_C_ABI_SYMBOL"
        and fact.to_identity == "native_start"
        for fact in relations
    )
    assert any(
        fact.fact_kind == "EXPORTS_C_ABI_SYMBOL"
        and fact.to_identity == "demo_alias"
        for fact in relations
    )
    assert any(
        fact.fact_kind == "IMPORTS_C_ABI_SYMBOL"
        and fact.to_identity == "platform_open"
        for fact in relations
    )


def test_unknown_cfg_is_candidate_and_not_active_function() -> None:
    result = parse_fixture()

    assert any(
        isinstance(fact, CandidateFact)
        and fact.candidate_kind == "unknown_cfg_item"
        and "future_android" in fact.properties["cfg_expression"]
        for fact in result.facts
    )
    assert not any(
        isinstance(fact, SymbolFact)
        and fact.fact_kind == "RUST_FUNCTION"
        and fact.logical_identity.endswith("::conditional()")
        for fact in result.facts
    )


def test_unexpanded_macro_is_recorded_without_inventing_generated_symbols() -> None:
    result = parse_fixture()

    assert any(
        isinstance(fact, SymbolFact)
        and fact.fact_kind == "RUST_MACRO_INVOCATION"
        and fact.logical_identity.endswith("generate_bindings")
        for fact in result.facts
    )
    assert any(
        isinstance(item, DiagnosticFact)
        and item.reason_code == "unexpanded_rust_macro"
        for item in result.diagnostics
    )


def test_rust_parser_is_deterministic_and_records_grammar_identity() -> None:
    first = parse_fixture()
    second = parse_fixture()

    assert first == second
    assert first.grammar_name == "tree-sitter-rust"
    assert len(first.grammar_fingerprint) == 64
