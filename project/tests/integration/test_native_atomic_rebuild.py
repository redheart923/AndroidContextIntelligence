from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from workspace.native_pipeline import (
    NativePipelineError,
    _repositories_from_plan,
    run_native_pipeline,
)
from workspace.schema_migrations import apply_migrations


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/native"
BUILD_FIXTURE = FIXTURE / "build"


def database(tmp_path: Path) -> Path:
    path = tmp_path / "live.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript((ROOT / "storage/schema.sql").read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    apply_migrations(path, ROOT / "storage/migrations")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_partial_fixture_publishes_atomically_and_second_run_is_deterministic(tmp_path: Path) -> None:
    path = database(tmp_path)
    reports = tmp_path / "reports"

    first = run_native_pipeline(
        path,
        repositories=(("frameworks/native", FIXTURE, "d" * 40),),
        raw_root=reports,
        strict_capabilities=("native_symbols", "soong_build_graph", "jni_bindings"),
    )
    second = run_native_pipeline(
        path,
        repositories=(("frameworks/native", FIXTURE, "d" * 40),),
        raw_root=reports,
        strict_capabilities=("native_symbols", "soong_build_graph", "jni_bindings"),
    )

    assert first.status == "published"
    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert first.optional_inputs_missing == ()
    assert first.parser_identities
    assert all(len(fingerprint) == 64 for _, fingerprint in first.parser_identities)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM effective_node WHERE node_type='EXTRACTION_CANDIDATE'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM effective_edge WHERE edge_type='JNI_BINDS_TO'").fetchone()[0] >= 1


def test_failure_keeps_previous_live_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = database(tmp_path)
    before = digest(path)

    def fail(*args, **kwargs):
        raise RuntimeError("forced parser failure")

    monkeypatch.setattr("workspace.native_pipeline.parse_cpp_file", fail)
    with pytest.raises(NativePipelineError, match="forced parser failure"):
        run_native_pipeline(
            path,
            repositories=(("frameworks/native", FIXTURE, "d" * 40),),
            raw_root=tmp_path / "reports",
        )

    assert digest(path) == before


def test_committed_wal_state_is_included_in_published_database(tmp_path: Path) -> None:
    path = database(tmp_path)
    writer = sqlite3.connect(path)
    try:
        writer.execute("CREATE TABLE wal_marker(value TEXT NOT NULL)")
        writer.execute("INSERT INTO wal_marker VALUES ('committed')")
        writer.commit()

        run_native_pipeline(
            path,
            repositories=(("frameworks/native", FIXTURE, "d" * 40),),
            raw_root=tmp_path / "reports",
        )
    finally:
        writer.close()

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM wal_marker").fetchone() == (
            "committed",
        )


def test_database_snapshot_does_not_copy_database_and_wal_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = database(tmp_path)
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE checkpoint_marker(value TEXT NOT NULL)")
    writer.execute("INSERT INTO checkpoint_marker VALUES ('committed')")
    writer.commit()
    def reject_file_copy(*args, **kwargs):
        raise AssertionError("SQLite snapshots must use Connection.backup()")

    monkeypatch.setattr(shutil, "copy2", reject_file_copy)
    try:
        run_native_pipeline(
            path,
            repositories=(("frameworks/native", FIXTURE, "d" * 40),),
            raw_root=tmp_path / "reports",
        )
    finally:
        writer.close()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM checkpoint_marker"
        ).fetchone() == ("committed",)


def test_optional_missing_build_input_is_reported_not_fatal(tmp_path: Path) -> None:
    path = database(tmp_path)
    config = tmp_path / "build_inputs.toml"
    config.write_text(
        '[[inputs]]\nkind = "ninja"\npath = "missing/build.ninja"\noptional = true\n',
        encoding="utf-8",
    )

    report = run_native_pipeline(
        path,
        repositories=(("frameworks/native", FIXTURE, "d" * 40),),
        raw_root=tmp_path / "reports",
        build_inputs=config,
    )

    assert report.status == "published"
    assert report.optional_inputs_missing == ("missing/build.ninja",)
    payload = json.loads((tmp_path / "reports/native-pipeline-report.json").read_text())
    assert payload["optional_inputs_missing"] == ["missing/build.ninja"]


def test_all_supported_build_metadata_inputs_are_published(tmp_path: Path) -> None:
    path = database(tmp_path)
    config = tmp_path / "build_inputs.toml"
    entries = []
    for kind, name in (
        ("ninja", "build.ninja"),
        ("compile_commands", "compile_commands.json"),
        ("rust_project", "rust-project.json"),
        ("module_info", "module-info.json"),
    ):
        entries.append(
            f'''[[inputs]]
kind = "{kind}"
path = "{(BUILD_FIXTURE / name).as_posix()}"
repository = "frameworks/native"
source_revision = "{'d' * 40}"
'''
        )
    config.write_text("\n".join(entries), encoding="utf-8")

    report = run_native_pipeline(
        path,
        repositories=(("frameworks/native", FIXTURE, "d" * 40),),
        raw_root=tmp_path / "reports",
        build_inputs=config,
    )

    with sqlite3.connect(path) as connection:
        counts = dict(
            connection.execute(
                """
                SELECT node_type, COUNT(*)
                FROM effective_node
                WHERE node_type IN (
                  'BUILD_ACTION', 'TRANSLATION_UNIT',
                  'RUST_CRATE_METADATA', 'SOONG_MODULE_METADATA'
                )
                GROUP BY node_type
                """
            )
        )
    assert counts["BUILD_ACTION"] >= 1
    assert counts["TRANSLATION_UNIT"] >= 1
    assert counts["RUST_CRATE_METADATA"] >= 1
    assert counts["SOONG_MODULE_METADATA"] >= 1
    assert {kind for kind, _, _ in report.build_input_identities} == {
        "compile_commands",
        "module_info",
        "ninja",
        "rust_project",
    }


def test_compile_commands_selects_header_grammar(tmp_path: Path) -> None:
    repository = tmp_path / "aosp/demo/repo"
    (repository / "src").mkdir(parents=True)
    (repository / "include/demo").mkdir(parents=True)
    (repository / "src/main.cpp").write_text(
        '#include "demo/Foo.h"\nint main() { return demo::Foo::value(); }\n',
        encoding="utf-8",
    )
    (repository / "include/demo/Foo.h").write_text(
        "namespace demo { struct Foo { static int value(); }; }\n",
        encoding="utf-8",
    )
    compile_commands = tmp_path / "compile_commands.json"
    compile_commands.write_text(
        json.dumps(
            [
                {
                    "directory": str(repository),
                    "file": "src/main.cpp",
                    "command": "clang++ -Iinclude -c src/main.cpp",
                }
            ]
        ),
        encoding="utf-8",
    )
    config = tmp_path / "build_inputs.toml"
    config.write_text(
        f'''[[inputs]]
kind = "compile_commands"
path = "{compile_commands.as_posix()}"
repository = "demo/repo"
''',
        encoding="utf-8",
    )
    path = database(tmp_path)

    run_native_pipeline(
        path,
        repositories=(("demo/repo", repository, None),),
        raw_root=tmp_path / "reports",
        build_inputs=config,
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM effective_node
            WHERE node_type='CPP_TYPE'
              AND source_path='demo/repo/include/demo/Foo.h'
            """
        ).fetchone()[0] == 1


def test_soong_owner_selects_header_grammar_without_compile_commands(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "aosp/demo/repo"
    (repository / "src").mkdir(parents=True)
    (repository / "include").mkdir()
    (repository / "Android.bp").write_text(
        '''cc_library {
    name: "libdemo",
    srcs: ["src/main.c"],
    local_include_dirs: ["include"],
}
''',
        encoding="utf-8",
    )
    (repository / "src/main.c").write_text(
        '#include "demo.h"\nint main(void) { return demo_value(); }\n',
        encoding="utf-8",
    )
    (repository / "include/demo.h").write_text(
        "struct demo_value_holder { int value; };\n",
        encoding="utf-8",
    )
    path = database(tmp_path)

    run_native_pipeline(
        path,
        repositories=(("demo/repo", repository, None),),
        raw_root=tmp_path / "reports",
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM effective_node
            WHERE node_type='C_TYPE'
              AND source_path='demo/repo/include/demo.h'
            """
        ).fetchone()[0] == 1


def test_rust_project_context_activates_known_cfg_item(tmp_path: Path) -> None:
    repository = tmp_path / "aosp/demo/repo"
    (repository / "rust/src").mkdir(parents=True)
    source = repository / "rust/src/lib.rs"
    source.write_text(
        '#[cfg(android)]\npub fn enabled_on_android() {}\n',
        encoding="utf-8",
    )
    rust_project = tmp_path / "rust-project.json"
    rust_project.write_text(
        json.dumps(
            {
                "crates": [
                    {
                        "crate_id": 0,
                        "display_name": "demo_native",
                        "root_module": "demo/repo/rust/src/lib.rs",
                        "edition": "2021",
                        "cfg": ["android"],
                        "deps": [],
                        "is_workspace_member": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "build_inputs.toml"
    config.write_text(
        f'''[[inputs]]
kind = "rust_project"
path = "{rust_project.as_posix()}"
repository = "demo/repo"
''',
        encoding="utf-8",
    )
    path = database(tmp_path)

    run_native_pipeline(
        path,
        repositories=(("demo/repo", repository, None),),
        raw_root=tmp_path / "reports",
        build_inputs=config,
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM effective_node
            WHERE node_type='RUST_FUNCTION'
              AND qualified_name LIKE '%enabled_on_android%'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM effective_node
            WHERE node_type='EXTRACTION_CANDIDATE'
              AND properties_json LIKE '%unknown_cfg_item%'
            """
        ).fetchone()[0] == 0


def test_future_unknown_blueprint_degrades_without_false_active_edges(tmp_path: Path) -> None:
    path = database(tmp_path)
    future = ROOT / "tests/fixtures/native/versions/future-unknown"

    report = run_native_pipeline(
        path,
        repositories=(("future/module", future, "e" * 40),),
        raw_root=tmp_path / "reports",
    )

    assert report.status == "published"
    assert report.candidate_count >= 1
    assert "soong_build_graph" in report.degraded_capabilities


def test_execution_plan_include_exclude_and_language_scope_are_honored(
    tmp_path: Path,
) -> None:
    aosp = tmp_path / "aosp"
    repository = aosp / "demo/repo"
    (repository / "src").mkdir(parents=True)
    (repository / "excluded").mkdir()
    (repository / "src/Keep.c").write_text(
        "int keep(void) { return 1; }\n", encoding="utf-8"
    )
    (repository / "src/Skip.cpp").write_text(
        "int skip() { return 2; }\n", encoding="utf-8"
    )
    (repository / "excluded/Drop.c").write_text(
        "int drop(void) { return 3; }\n", encoding="utf-8"
    )
    plan = {
        "aosp_root": str(aosp),
        "default_exclude": ["excluded"],
        "repositories": [
            {
                "name": "demo/repo",
                "path": "demo/repo",
                "enabled": True,
                "status": "available",
                "revision": "f" * 40,
                "include": ["src", "excluded"],
                "exclude": [],
                "languages": ["c"],
            }
        ],
    }
    path = database(tmp_path)

    run_native_pipeline(
        path,
        repositories=_repositories_from_plan(plan),
        raw_root=tmp_path / "reports",
    )

    with sqlite3.connect(path) as connection:
        sources = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT source_path
                FROM effective_node
                WHERE source_path LIKE 'demo/repo/%'
                """
            )
        }
    assert any(source.endswith("src/Keep.c") for source in sources)
    assert not any("excluded/Drop.c" in source for source in sources)
    assert not any(source.endswith("src/Skip.cpp") for source in sources)


@pytest.mark.parametrize("version", ["legacy-known", "current-known"])
def test_known_blueprint_versions_publish_modules(tmp_path: Path, version: str) -> None:
    path = database(tmp_path)
    report = run_native_pipeline(
        path,
        repositories=(("demo", FIXTURE / "versions" / version, "d" * 40),),
        raw_root=tmp_path / "reports",
        strict_capabilities=("soong_build_graph",),
    )
    assert report.validation.valid
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM effective_node WHERE node_type='SOONG_MODULE'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("invalid", ["hash", "revision"])
def test_untrusted_build_input_preserves_previous_database(
    tmp_path: Path, invalid: str,
) -> None:
    path = database(tmp_path)
    before = digest(path)
    config = tmp_path / "inputs.toml"
    revision = "e" * 40 if invalid == "revision" else "d" * 40
    entry = (
        '[[inputs]]\nkind = "ninja"\n'
        f'path = "{(BUILD_FIXTURE / "build.ninja").as_posix()}"\n'
        'repository = "frameworks/native"\n'
        f'source_revision = "{revision}"\n'
    )
    if invalid == "hash":
        entry += f'sha256 = "{"0" * 64}"\n'
    config.write_text(entry, encoding="utf-8")
    with pytest.raises(NativePipelineError, match="mismatch"):
        run_native_pipeline(
            path,
            repositories=(("frameworks/native", FIXTURE, "d" * 40),),
            raw_root=tmp_path / "reports",
            build_inputs=config,
        )
    assert digest(path) == before


def test_uncommitted_source_change_changes_correction_scope(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    source = repository / "demo.c"
    source.write_text("int demo(void) { return 1; }\n", encoding="utf-8")
    fingerprints = []
    for index in range(2):
        target = tmp_path / str(index)
        target.mkdir()
        path = database(target)
        run_native_pipeline(
            path, repositories=(("demo", repository, "d" * 40),),
            raw_root=target / "reports",
        )
        with sqlite3.connect(path) as connection:
            fingerprints.append(connection.execute(
                "SELECT source_fingerprint FROM extraction_run WHERE capability='native_static_graph'"
            ).fetchone()[0])
        source.write_text("int demo(void) { return 2; }\n", encoding="utf-8")
    assert fingerprints[0] != fingerprints[1]


def test_build_input_change_changes_correction_scope(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "demo.c").write_text(
        "int demo(void) { return 1; }\n", encoding="utf-8"
    )
    ninja = tmp_path / "build.ninja"
    config = tmp_path / "build_inputs.toml"
    config.write_text(
        f'''[[inputs]]
kind = "ninja"
path = "{ninja.as_posix()}"
repository = "demo"
''',
        encoding="utf-8",
    )
    fingerprints = []
    for index, command in enumerate(("cc -c $in -o $out", "clang -c $in -o $out")):
        ninja.write_text(
            f"rule cc\n  command = {command}\nbuild out/demo.o: cc demo.c\n",
            encoding="utf-8",
        )
        target = tmp_path / str(index)
        target.mkdir()
        path = database(target)
        run_native_pipeline(
            path,
            repositories=(("demo", repository, None),),
            raw_root=target / "reports",
            build_inputs=config,
        )
        with sqlite3.connect(path) as connection:
            fingerprints.append(
                connection.execute(
                    """
                    SELECT source_fingerprint FROM extraction_run
                    WHERE capability='native_static_graph'
                    """
                ).fetchone()[0]
            )
    assert fingerprints[0] != fingerprints[1]
