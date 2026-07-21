from __future__ import annotations

from pathlib import Path

from scripts.project_payload import (
    PAYLOAD_DIRECTORIES,
    PAYLOAD_FILES,
    compare_payload,
    iter_payload_files,
    payload_hashes,
)


def write(root: Path, relative_path: str, content: str = "content\n") -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def relative_paths(root: Path) -> tuple[str, ...]:
    return tuple(
        path.relative_to(root).as_posix()
        for path in iter_payload_files(root)
    )


def test_payload_contract_declares_canonical_project_entries() -> None:
    assert PAYLOAD_DIRECTORIES == (
        "collectors",
        "config",
        "configs",
        "graph",
        "queries",
        "scripts",
        "storage",
        "tests",
        "workspace",
    )
    assert PAYLOAD_FILES == (
        ".gitignore",
        "INSTALLATION_MANIFEST.txt",
        "README.md",
        "requirements-lock.txt",
    )


def test_iter_payload_files_returns_only_managed_files_in_stable_order(
    tmp_path: Path,
) -> None:
    write(tmp_path, "workspace/zeta.py")
    write(tmp_path, "collectors/alpha.py")
    write(tmp_path, "README.md")
    write(tmp_path, "requirements-lock.txt")

    assert relative_paths(tmp_path) == (
        "README.md",
        "collectors/alpha.py",
        "requirements-lock.txt",
        "workspace/zeta.py",
    )


def test_iter_payload_files_excludes_runtime_and_generated_content(
    tmp_path: Path,
) -> None:
    write(tmp_path, "workspace/module.py")
    write(tmp_path, "workspace/__pycache__/module.pyc")
    write(tmp_path, "workspace/module.pyc")
    write(tmp_path, "workspace/module.py.bak")
    write(tmp_path, "workspace/module.bak.20260721")
    write(tmp_path, "tests/.pytest_cache/state")
    write(tmp_path, "data/android_context.db")
    write(tmp_path, ".venv/bin/python")
    write(tmp_path, ".git/config")
    write(tmp_path, "backups/project.tar.gz")
    write(tmp_path, "unmanaged/notes.txt")

    assert relative_paths(tmp_path) == ("workspace/module.py",)


def test_payload_hashes_use_posix_paths_and_are_content_sensitive(
    tmp_path: Path,
) -> None:
    write(tmp_path, "scripts/tool.py", "first\n")

    first = payload_hashes(tmp_path)
    write(tmp_path, "scripts/tool.py", "second\n")
    second = payload_hashes(tmp_path)

    assert tuple(first) == ("scripts/tool.py",)
    assert first["scripts/tool.py"] != second["scripts/tool.py"]


def test_compare_payload_classifies_added_removed_and_modified_files(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    write(expected, "workspace/unchanged.py", "same\n")
    write(actual, "workspace/unchanged.py", "same\n")
    write(expected, "workspace/removed.py", "expected\n")
    write(actual, "workspace/added.py", "actual\n")
    write(expected, "scripts/modified.py", "before\n")
    write(actual, "scripts/modified.py", "after\n")
    write(actual, "data/runtime.db", "ignored\n")
    write(actual, ".venv/bin/python", "ignored\n")

    diff = compare_payload(expected, actual)

    assert diff.added == ("workspace/added.py",)
    assert diff.removed == ("workspace/removed.py",)
    assert diff.modified == ("scripts/modified.py",)
    assert not diff.is_clean


def test_compare_payload_is_clean_for_equal_managed_trees(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    write(expected, "graph/writer.py", "same\n")
    write(actual, "graph/writer.py", "same\n")
    write(actual, "data/runtime.db", "ignored\n")

    diff = compare_payload(expected, actual)

    assert diff.added == ()
    assert diff.removed == ()
    assert diff.modified == ()
    assert diff.is_clean

