from __future__ import annotations

from pathlib import Path

from collectors.build.blueprint_parser import parse_blueprint
from collectors.build.soong_resolver import resolve_soong


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native/build/Android.bp"
REPOSITORIES = {
    "frameworks/native": {
        "revision": "d" * 40,
        "platform_identity": "android-current",
    }
}


def resolve_fixture():
    document = parse_blueprint(
        FIXTURE.read_text(encoding="utf-8"),
        "frameworks/native/Android.bp",
    )
    return resolve_soong((document,), REPOSITORIES)


def module(result, name: str):
    return next(item for item in result.modules if item.module_name == name)


def test_defaults_and_variables_merge_into_resolved_properties() -> None:
    result = resolve_fixture()
    libdemo = module(result, "libdemo")

    assert libdemo.properties["raw_properties"]["defaults"] == [
        "demo_defaults"
    ]
    assert set(libdemo.properties["resolved_properties"]["srcs"]) == {
        "base.cpp",
        "default.cpp",
        "extra.cpp",
        ":demo_sources",
    }
    assert libdemo.properties["resolved_properties"]["cflags"] == ["-DDEMO"]
    assert libdemo.properties["unresolved_expressions"] == []
    assert any(
        relation.fact_kind == "USES_DEFAULTS"
        and relation.from_identity == "soong:module:libdemo"
        and relation.to_identity == "soong:module:demo_defaults"
        for relation in result.relations
    )


def test_filegroup_genrule_and_language_dependencies_are_resolved() -> None:
    result = resolve_fixture()
    relations = {
        (item.fact_kind, item.from_identity, item.to_identity)
        for item in result.relations
    }

    assert (
        "EXPANDS_FILEGROUP",
        "soong:module:libdemo",
        "soong:module:demo_sources",
    ) in relations
    assert (
        "COMPILES_SOURCE",
        "soong:module:libdemo",
        "file:frameworks/native/group.cpp",
    ) in relations
    assert (
        "GENERATES",
        "soong:module:demo_gen",
        "build-artifact:frameworks/native/generated.cpp",
    ) in relations
    assert (
        "DEPENDS_ON",
        "soong:module:libdemo",
        "soong:module:libdependency",
    ) in relations
    assert (
        "DEPENDS_ON",
        "soong:module:librustdemo",
        "soong:module:libdependency",
    ) in relations
    assert (
        "DEPENDS_ON",
        "soong:module:demo-java",
        "soong:module:libdependency",
    ) in relations


def test_duplicate_module_is_not_silently_selected() -> None:
    first = parse_blueprint(
        'cc_library { name: "duplicate" }',
        "frameworks/native/one/Android.bp",
    )
    second = parse_blueprint(
        'cc_library { name: "duplicate" }',
        "frameworks/native/two/Android.bp",
    )

    result = resolve_soong((first, second), REPOSITORIES)

    assert not any(item.module_name == "duplicate" for item in result.modules)
    assert any(
        item.reason_code == "duplicate_soong_module"
        for item in result.diagnostics
    )
    assert any(
        item.candidate_kind == "duplicate_soong_module"
        for item in result.candidates
    )


def test_missing_module_dependency_is_diagnostic_not_active_edge() -> None:
    document = parse_blueprint(
        '''cc_library {
            name: "demo",
            shared_libs: ["missing"],
        }''',
        "frameworks/native/Android.bp",
    )

    result = resolve_soong((document,), REPOSITORIES)

    assert not any(item.fact_kind == "DEPENDS_ON" for item in result.relations)
    assert any(
        item.reason_code == "missing_soong_module"
        for item in result.diagnostics
    )


def test_defaults_cycle_is_bounded_and_preserved_as_candidate() -> None:
    document = parse_blueprint(
        '''cc_defaults { name: "a", defaults: ["b"] }
        cc_defaults { name: "b", defaults: ["a"] }
        cc_library { name: "demo", defaults: ["a"] }
        ''',
        "frameworks/native/Android.bp",
    )

    result = resolve_soong((document,), REPOSITORIES)

    assert any(
        item.reason_code == "soong_defaults_cycle"
        for item in result.diagnostics
    )
    assert any(
        item.candidate_kind == "soong_defaults_cycle"
        for item in result.candidates
    )


def test_conditional_properties_remain_unresolved_candidates() -> None:
    document = parse_blueprint(
        '''cc_library {
            name: "conditional",
            shared_libs: select(soong_config_variable("ns", "key"), {
                "enabled": ["libenabled"], default: [],
            }),
            arch: { arm64: { srcs: ["arm.cpp"] } },
            product_variables: { debuggable: { cflags: ["-DDEBUG"] } },
        }''',
        "frameworks/native/Android.bp",
    )

    result = resolve_soong((document,), REPOSITORIES)
    conditional = module(result, "conditional")

    assert set(conditional.properties["unresolved_expressions"]) == {
        "arch",
        "product_variables",
        "shared_libs",
    }
    assert len(
        [
            item
            for item in result.candidates
            if item.candidate_kind == "unresolved_conditional_property"
        ]
    ) == 3
    assert not any(
        item.fact_kind in {"DEPENDS_ON", "COMPILES_SOURCE"}
        and item.from_identity == "soong:module:conditional"
        for item in result.relations
    )
