from __future__ import annotations

from pathlib import Path

from collectors.build.ninja_parser import parse_ninja


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native/build/build.ninja"
REVISION = "d" * 40


def parse_fixture():
    return parse_ninja(
        FIXTURE.read_text(encoding="utf-8"),
        source_path="out/soong/build.ninja",
        repository="frameworks/native",
        revision=REVISION,
        platform_identity="android-current",
    )


def test_ninja_variables_rules_pool_includes_and_defaults_are_preserved() -> None:
    result = parse_fixture()
    kinds = {item.fact_kind for item in result.facts}

    assert {
        "BUILD_RULE",
        "BUILD_ACTION",
        "BUILD_ARTIFACT",
        "NINJA_POOL",
        "NINJA_INCLUDE",
        "NINJA_SUBNINJA",
        "NINJA_DEFAULT",
    } <= kinds
    rule = next(item for item in result.facts if item.fact_kind == "BUILD_RULE")
    assert rule.properties["description"] == "compile C++"
    assert rule.properties["pool"] == "local_pool"
    assert len(str(rule.properties["command_sha256"])) == 64
    assert "command" not in rule.properties
    assert "token" not in str(rule.properties).lower()


def test_ninja_build_distinguishes_explicit_implicit_and_order_only_inputs() -> None:
    result = parse_fixture()
    action = next(
        item
        for item in result.facts
        if item.fact_kind == "BUILD_ACTION" and "out/demo.o" in item.outputs
    )

    assert action.inputs == ("generated.h", "order.stamp", "src/demo.cpp")
    assert action.outputs == ("out/demo.d", "out/demo.o")
    assert action.properties["explicit_inputs"] == ["src/demo.cpp"]
    assert action.properties["implicit_inputs"] == ["generated.h"]
    assert action.properties["order_only_inputs"] == ["order.stamp"]
    assert action.properties["implicit_outputs"] == ["out/demo.d"]
    assert action.properties["bindings"]["cflags"] == "-Wall -Wextra"
    assert any(
        item.fact_kind == "USES_RULE"
        and item.from_identity == action.logical_identity
        for item in result.relations
    )


def test_ninja_phony_action_and_artifact_edges_are_explicit() -> None:
    result = parse_fixture()
    phony = next(
        item
        for item in result.facts
        if item.fact_kind == "BUILD_ACTION" and item.rule == "phony"
    )

    assert phony.properties["phony"] is True
    assert any(
        item.fact_kind == "PRODUCES"
        and item.from_identity == phony.logical_identity
        and item.to_identity == "build-artifact:all"
        for item in result.relations
    )
    assert any(
        item.fact_kind == "CONSUMES"
        and item.from_identity == phony.logical_identity
        and item.to_identity == "build-artifact:out/demo.o"
        for item in result.relations
    )


def test_duplicate_output_producer_is_candidate_not_active_produces_edge() -> None:
    result = parse_ninja(
        '''rule cc
  command = cc -c $in -o $out
build out/demo.o: cc one.cpp
build out/demo.o: cc two.cpp
''',
        source_path="out/build.ninja",
        repository="frameworks/native",
        revision=REVISION,
        platform_identity="android-current",
    )

    assert any(
        item.candidate_kind == "duplicate_output_producer"
        for item in result.candidates
    )
    assert any(
        item.reason_code == "duplicate_output_producer"
        for item in result.diagnostics
    )
    assert not any(
        item.fact_kind == "PRODUCES"
        and item.to_identity == "build-artifact:out/demo.o"
        for item in result.relations
    )


def test_ninja_parse_error_fails_closed_with_diagnostic() -> None:
    result = parse_ninja(
        "build missing-colon cc input.cpp\n",
        source_path="out/build.ninja",
        repository="frameworks/native",
        revision=REVISION,
        platform_identity="android-current",
    )

    assert result.facts == ()
    assert result.relations == ()
    assert result.diagnostics[0].reason_code == "invalid_ninja_statement"
