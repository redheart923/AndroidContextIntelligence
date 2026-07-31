from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from .languages import SUFFIXES, _excluded, source_paths
from .models import RepositoryProvenance


REVISION = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


def resolve_repository_revision(repository: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    if result.returncode != 0 or REVISION.fullmatch(value) is None:
        return None
    return value.lower()


def _is_relevant_source(path: Path, whitelist: tuple[str, ...]) -> bool:
    if path.name == "Android.bp":
        language = "blueprint"
    elif path.name == "Android.mk":
        language = "make"
    else:
        language = SUFFIXES.get(path.suffix.lower())
    return language is not None and (not whitelist or language in whitelist)


def source_inventory_digest(
    repository: Path,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    whitelist: tuple[str, ...] = (),
) -> tuple[str, int]:
    files: list[Path] = []
    for root in source_paths(repository, include):
        for current, directories, names in os.walk(root):
            current_path = Path(current)
            directories[:] = sorted(
                name
                for name in directories
                if not _excluded(
                    (current_path / name).relative_to(repository),
                    exclude,
                )
            )
            for name in sorted(names):
                path = current_path / name
                relative = path.relative_to(repository)
                if (
                    not _excluded(relative, exclude)
                    and _is_relevant_source(path, whitelist)
                ):
                    files.append(path)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(repository).as_posix()):
        relative = path.relative_to(repository).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content_digest = hashlib.sha256(path.read_bytes()).digest()
        digest.update(content_digest)
    return digest.hexdigest(), len(files)


def _git_dirty(repository: Path) -> bool | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout)


def inspect_repository_provenance(
    repository: Path,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    whitelist: tuple[str, ...] = (),
) -> RepositoryProvenance:
    if not repository.is_dir():
        return RepositoryProvenance("missing", None, None, None, 0)
    inventory_sha256, file_count = source_inventory_digest(
        repository,
        include,
        exclude,
        whitelist,
    )
    revision = resolve_repository_revision(repository)
    if revision is None:
        return RepositoryProvenance(
            "non_git",
            None,
            None,
            inventory_sha256,
            file_count,
        )
    dirty = _git_dirty(repository)
    if dirty is None:
        state = "unknown"
    else:
        state = "dirty" if dirty else "clean"
    return RepositoryProvenance(
        state,
        revision,
        dirty,
        inventory_sha256,
        file_count,
    )
