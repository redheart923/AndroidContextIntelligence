from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.prepare_codeql import prepare_database
from workspace.codeql_database import (
    CodeQLDatabaseError,
    PreparationRequest,
    RepositoryIdentity,
)


def request(tmp_path: Path) -> PreparationRequest:
    aosp = tmp_path / "aosp"
    aosp.mkdir()
    codeql = tmp_path / "codeql"
    codeql.write_text("fixture", encoding="utf-8")
    return PreparationRequest(
        aosp_root=aosp,
        codeql_bin=codeql,
        product="aosp_cf_x86_64_phone",
        variant="userdebug",
        build_targets=("services", "SystemUI"),
        threads=4,
        ram_mb=8192,
        cache_root=tmp_path / "cache",
        repositories=(
            RepositoryIdentity(
                name="frameworks/base",
                path="frameworks/base",
                revision="abc",
                dirty=False,
                inventory_sha256="1" * 64,
                file_count=10,
            ),
        ),
        codeql_version="2.23.1",
        extractor_version="java-kotlin:fixture",
    )


class FakeRunner:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.fail_create = fail_create

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(tuple(command))
        if command[1:3] == ["database", "create"]:
            if self.fail_create:
                raise subprocess.CalledProcessError(17, command)
            database = Path(command[3])
            database.mkdir(parents=True)
            (database / "codeql-database.yml").write_text(
                "primaryLanguage: java\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["database", "info"]:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"languages": ["java-kotlin"]}),
                "",
            )
        raise AssertionError(f"unexpected command: {command}")


def test_prepare_database_publishes_and_reuses_verified_cache(
    tmp_path: Path,
) -> None:
    value = request(tmp_path)
    runner = FakeRunner()

    first = prepare_database(value, runner=runner)
    command_count = len(runner.commands)
    second = prepare_database(value, runner=runner)

    assert first == second
    assert (first.parent / "manifest.json").is_file()
    assert len(runner.commands) == command_count
    assert command_count == 2


def test_prepare_database_removes_partial_entry_after_failure(
    tmp_path: Path,
) -> None:
    value = request(tmp_path)

    with pytest.raises(CodeQLDatabaseError, match="database create"):
        prepare_database(value, runner=FakeRunner(fail_create=True))

    database_root = value.cache_root / "databases"
    assert not database_root.exists() or list(database_root.iterdir()) == []


def test_prepare_database_rejects_tampered_cached_marker(tmp_path: Path) -> None:
    value = request(tmp_path)
    database = prepare_database(value, runner=FakeRunner())
    (database / "codeql-database.yml").write_text(
        "primaryLanguage: tampered\n",
        encoding="utf-8",
    )

    with pytest.raises(CodeQLDatabaseError, match="marker"):
        prepare_database(value, runner=FakeRunner())
