from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from collectors.facts.model import (
    CandidateFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    RelationFact,
    SourceRange,
    SymbolFact,
)


SEMANTIC_PROFILE = "native-static-v0.1"
_SENSITIVE = re.compile(r"(?:token|secret|password|passwd|api[_-]?key)", re.I)


@dataclass(frozen=True)
class BuildMetadataResult:
    facts: tuple[Fact, ...]
    relations: tuple[RelationFact, ...]
    candidates: tuple[CandidateFact, ...]
    diagnostics: tuple[DiagnosticFact, ...]


def _load_payload(path: Path, revision: str | None, collection: str) -> tuple[list[object], bytes]:
    content = path.read_bytes()
    payload = json.loads(content)
    if isinstance(payload, list):
        return payload, content
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain an object or array")
    artifact_revision = payload.get("source_revision")
    if artifact_revision and revision and str(artifact_revision) != revision:
        raise ValueError(
            f"build metadata revision mismatch: expected {revision}, got {artifact_revision}"
        )
    values = payload.get(collection, [])
    if not isinstance(values, list):
        raise ValueError(f"{collection} must be an array")
    return values, content


def _evidence(
    path: Path,
    content: bytes,
    repository: str,
    revision: str | None,
    platform_identity: str,
) -> Evidence:
    return Evidence(
        repository=repository,
        extractor="build-metadata-json",
        extractor_version="0.1",
        evidence_kind=EvidenceKind.GENERATED_BUILD_ARTIFACT,
        source_revision=revision,
        content_fingerprint=hashlib.sha256(content).hexdigest(),
        platform_identity=platform_identity,
        semantic_profile_version=SEMANTIC_PROFILE,
    )


def _workspace_file(directory: str, filename: str, repository: str) -> str:
    candidate = PurePosixPath(directory.replace("\\", "/")) / filename.replace("\\", "/")
    parts = candidate.parts
    repository_parts = PurePosixPath(repository).parts
    for index in range(len(parts) - len(repository_parts) + 1):
        if parts[index:index + len(repository_parts)] == repository_parts:
            return PurePosixPath(*parts[index:]).as_posix()
    return (PurePosixPath(repository) / filename).as_posix()


def _command_tokens(entry: Mapping[str, object]) -> tuple[list[str], str]:
    command = entry.get("command")
    if isinstance(command, str):
        return shlex.split(command), command
    arguments = entry.get("arguments")
    if isinstance(arguments, list) and all(isinstance(item, str) for item in arguments):
        values = [str(item) for item in arguments]
        return values, "\0".join(values)
    raise ValueError("compile command requires command or arguments")


def load_compile_commands(
    path: Path,
    repository: str,
    revision: str | None,
    platform_identity: str,
) -> BuildMetadataResult:
    entries, content = _load_payload(path, revision, "commands")
    evidence = _evidence(path, content, repository, revision, platform_identity)
    location = SourceRange(source_path=path.as_posix(), line_start=1, line_end=1)
    facts: list[Fact] = []
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise ValueError("compile command entry must be an object")
        filename = str(raw.get("file", "")).strip()
        directory = str(raw.get("directory", "")).strip()
        if not filename or not directory:
            raise ValueError("compile command requires directory and file")
        tokens, command_material = _command_tokens(raw)
        includes: list[str] = []
        defines: list[str] = []
        standard: str | None = None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "-I" and index + 1 < len(tokens):
                includes.append(tokens[index + 1])
                index += 2
                continue
            if token.startswith("-I") and len(token) > 2:
                includes.append(token[2:])
            elif token == "-D" and index + 1 < len(tokens):
                define = tokens[index + 1]
                if not _SENSITIVE.search(define):
                    defines.append(define)
                index += 2
                continue
            elif token.startswith("-D") and len(token) > 2:
                define = token[2:]
                if not _SENSITIVE.search(define):
                    defines.append(define)
            elif token.startswith("-std="):
                standard = token.removeprefix("-std=")
            index += 1
        source_path = _workspace_file(directory, filename, repository)
        properties: dict[str, object] = {
            "command_sha256": hashlib.sha256(
                command_material.encode("utf-8")
            ).hexdigest(),
            "compile_directory": directory,
            "defines": defines,
            "include_paths": includes,
        }
        if standard:
            properties["language_standard"] = standard
        facts.append(
            SymbolFact(
                language="build-metadata",
                fact_kind="TRANSLATION_UNIT",
                logical_identity=f"translation-unit:{source_path}",
                source_range=location,
                evidence=evidence,
                properties=properties,
            )
        )
    return BuildMetadataResult(tuple(facts), (), (), ())
