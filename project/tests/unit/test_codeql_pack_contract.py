from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEQL_ROOT = PROJECT_ROOT / "codeql"


def test_query_pack_declares_all_versioned_exports() -> None:
    pack = (CODEQL_ROOT / "qlpack.yml").read_text(encoding="utf-8")

    assert "name: android-context/java-kotlin-call-dataflow" in pack
    assert "version: 0.1.0" in pack
    assert "codeql/java-all" in pack
    for name in ("CallSites.ql", "SystemServiceGuards.ql", "BinderIdentity.ql"):
        text = (CODEQL_ROOT / "queries" / name).read_text(encoding="utf-8")
        assert "@kind table" in text
        assert "schema_version" in text
    dataflow = (CODEQL_ROOT / "queries/SystemServiceDataflow.ql").read_text(
        encoding="utf-8"
    )
    assert "@kind path-problem" in dataflow
    assert "import SystemServicePath::PathGraph" in dataflow


def test_query_pack_has_locked_dependencies_and_fixture_expectations() -> None:
    assert (CODEQL_ROOT / "codeql-pack.lock.yml").is_file()
    for suite in ("call-sites", "security-flow"):
        root = CODEQL_ROOT / "tests" / suite
        assert tuple(root.glob("*.qlref"))
        assert tuple(root.glob("*.expected"))
        assert tuple(root.glob("*.java"))
        assert tuple(root.glob("*.kt"))


def test_system_service_models_name_required_security_apis() -> None:
    models = (CODEQL_ROOT / "lib/SystemServiceModels.qll").read_text(
        encoding="utf-8"
    )

    for token in (
        "getCallingUid",
        "getCallingPid",
        "clearCallingIdentity",
        "restoreCallingIdentity",
        "enforceCallingPermission",
        "checkCallingPermission",
        "noteOp",
        "handleIncomingUser",
    ):
        assert token in models


def test_call_site_fixture_locks_dispatch_and_language_semantics() -> None:
    rows = [
        line
        for line in (CODEQL_ROOT / "tests/call-sites/CallSites.expected")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert len(rows) == len(set(rows))
    assert any("constructor | must | 1" in row for row in rows)
    assert any("super | must | 1" in row for row in rows)
    assert any("virtual | may | 3" in row for row in rows)
    assert any("static | must | 1" in row and "overloaded" in row for row in rows)
    assert any("kotlin" in row and "#decorate(" in row for row in rows)
    assert any("no_source_backed_target" in row for row in rows)
    assert any("<anonymous>@Calls.java" in row for row in rows)


def test_semantic_symbol_key_includes_language_and_callable_kind() -> None:
    helpers = (CODEQL_ROOT / "lib/ExportHelpers.qll").read_text(encoding="utf-8")

    assert 'result = languageOf(c) + "|" + callableKindOf(c) + "|"' in helpers


def test_security_fixture_locks_positive_and_negative_semantics() -> None:
    root = CODEQL_ROOT / "tests/security-flow"
    guard_rows = (root / "SystemServiceGuards.expected").read_text(encoding="utf-8").splitlines()
    identity_rows = (root / "BinderIdentity.expected").read_text(encoding="utf-8").splitlines()
    flow_rows = (root / "SystemServiceDataflow.expected").read_text(encoding="utf-8").splitlines()

    assert len([row for row in guard_rows if row.strip()]) == 1
    assert "#guarded(" in guard_rows[0]
    assert all("#unguarded(" not in row for row in guard_rows)
    assert any("paired_all_exits" in row for row in identity_rows)
    assert any("missing_all_exit_restore" in row for row in identity_rows)
    assert any("#straightLineRestore(" in row and "missing_all_exit_restore" in row for row in identity_rows)
    assert any("SecurityService#unguarded(" in row for row in flow_rows)
    assert any("SecurityServiceKt#unguarded(" in row for row in flow_rows)
    assert any(row == "edges" for row in flow_rows)
    assert any(row == "nodes" for row in flow_rows)
    assert any("#throughHelper(" in row for row in flow_rows)


def test_acceptance_config_requires_positive_security_semantics() -> None:
    import tomllib

    config = tomllib.loads(
        (PROJECT_ROOT / "config/codeql.toml").read_text(encoding="utf-8")
    )
    positive = [
        item
        for item in config["acceptance"]["strong_evidence"]
        if item["kind"] == "security_trace"
    ]

    assert positive
    assert all(item.get("required_step_kinds") for item in positive)
