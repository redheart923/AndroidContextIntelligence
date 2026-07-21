from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


PAYLOAD_DIRECTORIES = (
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

PAYLOAD_FILES = (
    ".gitignore",
    "INSTALLATION_MANIFEST.txt",
    "README.md",
    "requirements-lock.txt",
)

_EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "backups",
    "data",
    "raw",
    "venv",
}

_EXCLUDED_FILE_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".pyc",
    ".pyo",
    ".tar",
    ".tar.gz",
    ".zip",
)


@dataclass(frozen=True)
class PayloadDiff:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not (self.added or self.removed or self.modified)


def _is_excluded(relative_path: Path) -> bool:
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in relative_path.parts[:-1]):
        return True

    name = relative_path.name
    if name.endswith(_EXCLUDED_FILE_SUFFIXES):
        return True
    if name.endswith(".bak") or ".bak." in name or ".backup." in name:
        return True
    return False


def iter_payload_files(root: Path) -> tuple[Path, ...]:
    root = Path(root)
    candidates: list[Path] = []

    for relative_name in PAYLOAD_FILES:
        path = root / relative_name
        if path.is_file() and not path.is_symlink():
            candidates.append(path)

    for directory_name in PAYLOAD_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative_path = path.relative_to(root)
            if not _is_excluded(relative_path):
                candidates.append(path)

    return tuple(sorted(candidates, key=lambda path: path.relative_to(root).as_posix()))


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_hashes(root: Path) -> dict[str, str]:
    root = Path(root)
    return {
        path.relative_to(root).as_posix(): _file_sha256(path)
        for path in iter_payload_files(root)
    }


def compare_payload(expected: Path, actual: Path) -> PayloadDiff:
    expected_hashes = payload_hashes(expected)
    actual_hashes = payload_hashes(actual)
    expected_paths = set(expected_hashes)
    actual_paths = set(actual_hashes)

    return PayloadDiff(
        added=tuple(sorted(actual_paths - expected_paths)),
        removed=tuple(sorted(expected_paths - actual_paths)),
        modified=tuple(
            sorted(
                path
                for path in expected_paths & actual_paths
                if expected_hashes[path] != actual_hashes[path]
            )
        ),
    )

