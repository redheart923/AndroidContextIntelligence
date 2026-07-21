from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any


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

DEFAULT_MANIFEST_NAME = ".android-context-installation.json"
MANIFEST_SCHEMA_VERSION = 1

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


@dataclass(frozen=True)
class PayloadManifest:
    schema_version: int
    source_commit: str
    files: dict[str, str]


class PayloadManifestError(ValueError):
    pass


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


def _compare_hashes(
    expected_hashes: dict[str, str],
    actual_hashes: dict[str, str],
) -> PayloadDiff:
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


def compare_payload(expected: Path, actual: Path) -> PayloadDiff:
    return _compare_hashes(payload_hashes(expected), payload_hashes(actual))


def write_manifest(
    payload_root: Path,
    output: Path,
    source_commit: str,
) -> None:
    if not source_commit:
        raise PayloadManifestError("source_commit must not be empty")

    document = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_commit": source_commit,
        "files": payload_hashes(payload_root),
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_manifest_document(document: Any) -> PayloadManifest:
    if not isinstance(document, dict):
        raise PayloadManifestError("manifest must be a JSON object")
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise PayloadManifestError(
            f"unsupported manifest schema: {document.get('schema_version')!r}"
        )

    source_commit = document.get("source_commit")
    if not isinstance(source_commit, str) or not source_commit:
        raise PayloadManifestError("manifest source_commit must be a non-empty string")

    files = document.get("files")
    if not isinstance(files, dict):
        raise PayloadManifestError("manifest files must be an object")
    normalized_files: dict[str, str] = {}
    for path, digest in files.items():
        if not isinstance(path, str) or not path or Path(path).is_absolute():
            raise PayloadManifestError(f"invalid manifest file path: {path!r}")
        if ".." in Path(path).parts:
            raise PayloadManifestError(f"unsafe manifest file path: {path!r}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise PayloadManifestError(f"invalid SHA-256 for manifest file: {path}")
        normalized_files[path] = digest

    return PayloadManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        source_commit=source_commit,
        files=dict(sorted(normalized_files.items())),
    )


def load_manifest(path: Path) -> PayloadManifest:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PayloadManifestError(f"cannot read manifest {path}: {error}") from error
    return _validate_manifest_document(document)


def verify_manifest(target: Path, manifest: PayloadManifest) -> PayloadDiff:
    return _compare_hashes(manifest.files, payload_hashes(target))
