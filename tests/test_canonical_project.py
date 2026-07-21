from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.project_payload import PAYLOAD_DIRECTORIES, PAYLOAD_FILES, iter_payload_files


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "project"


def test_canonical_project_contains_complete_payload_contract() -> None:
    missing = [
        relative_path
        for relative_path in (*PAYLOAD_FILES, *PAYLOAD_DIRECTORIES)
        if not (PROJECT / relative_path).exists()
    ]

    assert missing == []
    assert (PROJECT / "scripts/rebuild_all.sh").is_file()
    assert (PROJECT / "workspace/cli.py").is_file()
    assert (PROJECT / "graph/writer.py").is_file()


def test_canonical_project_does_not_contain_runtime_or_generated_files() -> None:
    forbidden_source_parts = {
        ".git",
        ".venv",
        "backups",
        "data",
        "venv",
    }
    forbidden_suffixes = {
        ".db",
        ".tar",
        ".gz",
        ".zip",
    }
    violations = []
    for path in PROJECT.rglob("*"):
        relative_path = path.relative_to(PROJECT)
        if forbidden_source_parts.intersection(relative_path.parts):
            violations.append(relative_path.as_posix())
        elif path.is_file() and relative_path.suffix in forbidden_suffixes:
            violations.append(relative_path.as_posix())

    payload_paths = {
        path.relative_to(PROJECT).as_posix()
        for path in iter_payload_files(PROJECT)
    }
    assert all(
        ".pytest_cache" not in Path(path).parts
        and "__pycache__" not in Path(path).parts
        and not path.endswith((".pyc", ".pyo"))
        for path in payload_paths
    )

    assert violations == []


def test_every_canonical_python_file_compiles() -> None:
    failures: list[str] = []

    for path in iter_payload_files(PROJECT):
        if path.suffix != ".py":
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (SyntaxError, UnicodeDecodeError) as error:
            failures.append(f"{path.relative_to(PROJECT).as_posix()}: {error}")

    assert failures == []


def test_every_canonical_shell_script_passes_bash_syntax_check() -> None:
    scripts = sorted(PROJECT.rglob("*.sh")) if PROJECT.exists() else []

    assert scripts
    for script in scripts:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
