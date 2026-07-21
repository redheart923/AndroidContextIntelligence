from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path
from .models import RepositorySpec


class ManifestError(RuntimeError):
    pass


def parse_repo_manifest(manifest_path: Path) -> tuple[RepositorySpec, ...]:
    found: dict[str, RepositorySpec] = {}
    active: list[Path] = []

    def repo_metadata_directory(path: Path) -> Path | None:
        for candidate in (path, *path.parents):
            if candidate.name == ".repo":
                return candidate
        return None

    def resolve_include(current_manifest: Path, include_name: str) -> Path:
        direct = current_manifest.parent / include_name
        if direct.is_file():
            return direct
        repo_dir = repo_metadata_directory(current_manifest)
        if repo_dir is not None:
            manifest_store = repo_dir / "manifests" / include_name
            if manifest_store.is_file():
                return manifest_store
            local_store = repo_dir / "local_manifests" / include_name
            if local_store.is_file():
                return local_store
        return direct

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in active:
            raise ManifestError(f"manifest include cycle: {resolved}")
        if not resolved.is_file():
            raise ManifestError(f"missing manifest include: {resolved}")
        active.append(resolved)
        try:
            root = ET.parse(resolved).getroot()
            for project in root.findall("project"):
                name = project.get("name")
                if not name:
                    continue
                repo_path = project.get("path") or name
                found.setdefault(repo_path, RepositorySpec(name=name, path=repo_path))
            for include in root.findall("include"):
                name = include.get("name")
                if name:
                    visit(resolve_include(resolved, name))
        except ET.ParseError as error:
            raise ManifestError(f"invalid manifest {resolved}: {error}") from error
        finally:
            active.pop()

    visit(manifest_path)
    return tuple(found[key] for key in sorted(found))
