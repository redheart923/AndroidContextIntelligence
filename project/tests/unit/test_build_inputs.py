from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from collectors.build.compile_commands import load_compile_commands
from collectors.build.module_info import load_module_info
from collectors.build.rust_project import load_rust_project
from workspace.build_inputs import (
    BuildInputError,
    inspect_build_inputs,
    load_build_inputs,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/native/build"
REVISION = "d" * 40


def test_optional_missing_input_is_skipped_but_required_input_fails(
    tmp_path: Path,
) -> None:
    config = tmp_path / "inputs.toml"
    config.write_text(
        '''[[inputs]]
kind = "ninja"
path = "missing.ninja"
optional = true
''',
        encoding="utf-8",
    )
    assert load_build_inputs(config) == ()

    config.write_text(
        config.read_text(encoding="utf-8").replace("true", "false"),
        encoding="utf-8",
    )
    with pytest.raises(BuildInputError, match="required build input is missing"):
        load_build_inputs(config)


def test_inspection_reports_optional_missing_inputs(tmp_path: Path) -> None:
    config = tmp_path / "inputs.toml"
    config.write_text(
        '''[[inputs]]
kind = "ninja"
path = "missing/build.ninja"
optional = true
''',
        encoding="utf-8",
    )

    result = inspect_build_inputs(config)

    assert result.inputs == ()
    assert result.optional_missing == ("missing/build.ninja",)


def test_build_input_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "build.ninja"
    artifact.write_text("rule cc\n  command = cc\n", encoding="utf-8")
    config = tmp_path / "inputs.toml"
    config.write_text(
        f'''[[inputs]]
kind = "ninja"
path = "{artifact.name}"
sha256 = "{'0' * 64}"
''',
        encoding="utf-8",
    )

    with pytest.raises(BuildInputError, match="SHA-256 mismatch"):
        load_build_inputs(config)


def test_build_input_normalizes_relative_path_and_metadata(tmp_path: Path) -> None:
    artifact = tmp_path / "build.ninja"
    artifact.write_text("default all\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    config = tmp_path / "inputs.toml"
    config.write_text(
        f'''[[inputs]]
kind = "ninja"
path = "build.ninja"
repository = "frameworks/native"
source_revision = "{REVISION}"
sha256 = "{digest}"
''',
        encoding="utf-8",
    )

    item = load_build_inputs(config)[0]

    assert item.path == artifact.resolve()
    assert item.kind == "ninja"
    assert item.repository == "frameworks/native"
    assert item.source_revision == REVISION
    assert item.content_fingerprint == digest


def test_compile_commands_redacts_secret_command_but_keeps_allowlisted_flags() -> None:
    result = load_compile_commands(
        FIXTURE / "compile_commands.json",
        repository="frameworks/native",
        revision=REVISION,
        platform_identity="android-current",
    )
    unit = next(item for item in result.facts if item.fact_kind == "TRANSLATION_UNIT")
    encoded = json.dumps(unit.properties, sort_keys=True)

    assert unit.logical_identity == "translation-unit:frameworks/native/src/demo.cpp"
    assert unit.properties["include_paths"] == ["include"]
    assert unit.properties["defines"] == ["DEBUG"]
    assert unit.properties["language_standard"] == "c++20"
    assert len(str(unit.properties["command_sha256"])) == 64
    assert "top-secret" not in encoded
    assert "API_TOKEN" not in encoded
    assert "command" not in unit.properties


def test_build_metadata_revision_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="revision mismatch"):
        load_compile_commands(
            FIXTURE / "compile_commands.json",
            repository="frameworks/native",
            revision="e" * 40,
            platform_identity="android-current",
        )


def test_rust_project_and_module_info_emit_modules_and_dependencies() -> None:
    rust = load_rust_project(
        FIXTURE / "rust-project.json",
        repository="frameworks/native",
        revision=REVISION,
        platform_identity="android-current",
    )
    module_info = load_module_info(
        FIXTURE / "module-info.json",
        repository="frameworks/native",
        revision=REVISION,
        platform_identity="android-current",
    )

    demo = next(item for item in rust.facts if item.module_name == "demo")
    assert demo.properties["edition"] == "2021"
    assert demo.properties["cfg"] == ["android", "feature=demo"]
    assert any(item.fact_kind == "RUST_CRATE_DEPENDS_ON" for item in rust.relations)

    libdemo = next(item for item in module_info.facts if item.module_name == "libdemo")
    assert libdemo.properties["module_paths"] == ["frameworks/native/demo"]
    assert libdemo.properties["installed_files"] == [
        "out/target/product/demo/system/lib64/libdemo.so"
    ]
    assert any(
        item.fact_kind == "MODULE_INFO_DESCRIBES"
        and item.from_identity == "module-info:soong:module:libdemo"
        and item.to_identity == "soong:module:libdemo"
        for item in module_info.relations
    )
    assert any(item.fact_kind == "MODULE_INFO_DEPENDS_ON" for item in module_info.relations)
