from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


_KINDS = {"ninja", "compile_commands", "rust_project", "module_info"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class BuildInputError(ValueError):
    """Raised when optional build metadata cannot be trusted."""


@dataclass(frozen=True)
class BuildInput:
    kind: str
    path: Path
    optional: bool
    repository: str | None
    source_revision: str | None
    content_fingerprint: str


def load_build_inputs(path: Path) -> tuple[BuildInput, ...]:
    config_path = path.resolve()
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    raw_inputs = payload.get("inputs", [])
    if not isinstance(raw_inputs, list):
        raise BuildInputError("inputs must be an array of tables")
    inputs: list[BuildInput] = []
    for index, raw in enumerate(raw_inputs):
        if not isinstance(raw, dict):
            raise BuildInputError(f"inputs[{index}] must be a table")
        kind = str(raw.get("kind", "")).strip()
        if kind not in _KINDS:
            raise BuildInputError(f"unsupported build input kind: {kind!r}")
        raw_path = str(raw.get("path", "")).strip()
        if not raw_path:
            raise BuildInputError(f"inputs[{index}].path must not be empty")
        artifact = Path(raw_path).expanduser()
        if not artifact.is_absolute():
            artifact = config_path.parent / artifact
        artifact = artifact.resolve()
        optional = bool(raw.get("optional", False))
        if not artifact.is_file():
            if optional:
                continue
            raise BuildInputError(f"required build input is missing: {artifact}")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        expected = raw.get("sha256")
        if expected is not None:
            expected = str(expected).lower()
            if not _SHA256.fullmatch(expected):
                raise BuildInputError(f"invalid SHA-256 for {artifact}")
            if digest != expected:
                raise BuildInputError(
                    f"SHA-256 mismatch for {artifact}: expected {expected}, got {digest}"
                )
        repository = raw.get("repository")
        revision = raw.get("source_revision")
        inputs.append(
            BuildInput(
                kind=kind,
                path=artifact,
                optional=optional,
                repository=str(repository) if repository else None,
                source_revision=str(revision) if revision else None,
                content_fingerprint=digest,
            )
        )
    return tuple(sorted(inputs, key=lambda item: (item.kind, item.path.as_posix())))
