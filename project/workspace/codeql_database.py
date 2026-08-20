from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


SAFE_BUILD_TOKEN = re.compile(r"^[A-Za-z0-9_.+:-]+$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


class CodeQLDatabaseError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_digest(name: str, value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise CodeQLDatabaseError(f"{name} must be a lowercase SHA-256")


def _sorted_repositories(
    repositories: tuple["RepositoryIdentity", ...],
) -> tuple["RepositoryIdentity", ...]:
    return tuple(sorted(repositories, key=lambda item: (item.path, item.name)))


@dataclass(frozen=True)
class RepositoryIdentity:
    name: str
    path: str
    revision: str
    dirty: bool
    inventory_sha256: str
    file_count: int

    def __post_init__(self) -> None:
        if not self.name or not self.path or not self.revision:
            raise CodeQLDatabaseError("repository identity is incomplete")
        _require_digest("inventory_sha256", self.inventory_sha256)
        if self.file_count < 1:
            raise CodeQLDatabaseError("repository file_count must be positive")

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RepositoryIdentity":
        return cls(
            name=str(value["name"]),
            path=str(value["path"]),
            revision=str(value["revision"]),
            dirty=bool(value["dirty"]),
            inventory_sha256=str(value["inventory_sha256"]),
            file_count=int(value["file_count"]),
        )


@dataclass(frozen=True)
class PreparationRequest:
    aosp_root: Path
    codeql_bin: Path
    product: str
    variant: str
    build_targets: tuple[str, ...]
    threads: int
    ram_mb: int
    cache_root: Path
    repositories: tuple[RepositoryIdentity, ...]
    codeql_version: str
    extractor_version: str

    def __post_init__(self) -> None:
        for name, value in (
            ("product", self.product),
            ("variant", self.variant),
            ("codeql_version", self.codeql_version),
            ("extractor_version", self.extractor_version),
        ):
            if not value:
                raise CodeQLDatabaseError(f"{name} must not be empty")
        if not self.aosp_root.is_dir():
            raise CodeQLDatabaseError(f"AOSP root is not a directory: {self.aosp_root}")
        if not self.codeql_bin.is_file():
            raise CodeQLDatabaseError(f"CodeQL executable is not a file: {self.codeql_bin}")
        if not self.repositories:
            raise CodeQLDatabaseError("repositories must not be empty")
        if not self.build_targets:
            raise CodeQLDatabaseError("build_targets must not be empty")
        for token in (self.product, self.variant, *self.build_targets):
            if SAFE_BUILD_TOKEN.fullmatch(token) is None:
                raise CodeQLDatabaseError(f"unsafe AOSP build token: {token!r}")
        if self.threads < 1:
            raise CodeQLDatabaseError("threads must be positive")
        if self.ram_mb < 1024:
            raise CodeQLDatabaseError("ram_mb must be at least 1024")

    @property
    def source_fingerprint(self) -> str:
        payload = [asdict(item) for item in _sorted_repositories(self.repositories)]
        return _sha256_bytes(_canonical_bytes(payload))


@dataclass(frozen=True)
class CodeQLDatabaseManifest:
    schema_version: int
    status: str
    cache_key: str
    language: str
    source_fingerprint: str
    product: str
    variant: str
    build_targets: tuple[str, ...]
    threads: int
    ram_mb: int
    codeql_version: str
    extractor_version: str
    database_fingerprint: str
    database_marker_sha256: str
    observed_java_files: int
    observed_kotlin_files: int
    repositories: tuple[RepositoryIdentity, ...]
    created_at: str
    database_info: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["build_targets"] = list(self.build_targets)
        result["repositories"] = [asdict(item) for item in self.repositories]
        return result

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "CodeQLDatabaseManifest":
        repositories = value.get("repositories")
        if not isinstance(repositories, list):
            raise CodeQLDatabaseError("manifest repositories must be a list")
        database_info = value.get("database_info")
        if not isinstance(database_info, dict):
            raise CodeQLDatabaseError("manifest database_info must be an object")
        return cls(
            schema_version=int(value["schema_version"]),
            status=str(value["status"]),
            cache_key=str(value["cache_key"]),
            language=str(value["language"]),
            source_fingerprint=str(value["source_fingerprint"]),
            product=str(value["product"]),
            variant=str(value["variant"]),
            build_targets=tuple(str(item) for item in value["build_targets"]),
            threads=int(value["threads"]),
            ram_mb=int(value["ram_mb"]),
            codeql_version=str(value["codeql_version"]),
            extractor_version=str(value["extractor_version"]),
            database_fingerprint=str(value["database_fingerprint"]),
            database_marker_sha256=str(value["database_marker_sha256"]),
            observed_java_files=int(value["observed_java_files"]),
            observed_kotlin_files=int(value["observed_kotlin_files"]),
            repositories=tuple(
                RepositoryIdentity.from_dict(item) for item in repositories
            ),
            created_at=str(value["created_at"]),
            database_info=dict(database_info),
        )


def preparation_fingerprint(request: PreparationRequest) -> str:
    payload = {
        "schema_version": 1,
        "language": "java-kotlin",
        "source_fingerprint": request.source_fingerprint,
        "repositories": [
            asdict(item) for item in _sorted_repositories(request.repositories)
        ],
        "product": request.product,
        "variant": request.variant,
        "build_targets": list(request.build_targets),
        "threads": request.threads,
        "ram_mb": request.ram_mb,
        "codeql_version": request.codeql_version,
        "extractor_version": request.extractor_version,
    }
    return _sha256_bytes(_canonical_bytes(payload))


def manifest_preparation_fingerprint(manifest: CodeQLDatabaseManifest) -> str:
    payload = {
        "schema_version": 1,
        "language": "java-kotlin",
        "source_fingerprint": manifest.source_fingerprint,
        "repositories": [
            asdict(item) for item in _sorted_repositories(manifest.repositories)
        ],
        "product": manifest.product,
        "variant": manifest.variant,
        "build_targets": list(manifest.build_targets),
        "threads": manifest.threads,
        "ram_mb": manifest.ram_mb,
        "codeql_version": manifest.codeql_version,
        "extractor_version": manifest.extractor_version,
    }
    return _sha256_bytes(_canonical_bytes(payload))


def validate_manifest_self_consistency(
    manifest: CodeQLDatabaseManifest, *, database: Path
) -> None:
    if manifest.schema_version != 2:
        raise CodeQLDatabaseError(
            f"unsupported CodeQL manifest schema: {manifest.schema_version}"
        )
    if not manifest.product or not manifest.variant or not manifest.build_targets:
        raise CodeQLDatabaseError("CodeQL manifest build identity is incomplete")
    if manifest.threads < 1 or manifest.ram_mb < 1024:
        raise CodeQLDatabaseError("CodeQL manifest resource identity is invalid")
    repositories = [asdict(item) for item in _sorted_repositories(manifest.repositories)]
    expected_source = _sha256_bytes(_canonical_bytes(repositories))
    if manifest.source_fingerprint != expected_source:
        raise CodeQLDatabaseError("CodeQL manifest source_fingerprint mismatch")
    expected_cache = manifest_preparation_fingerprint(manifest)
    if manifest.cache_key != expected_cache:
        raise CodeQLDatabaseError("CodeQL manifest cache_key mismatch")
    cache_directory = database.parent.name
    if (
        cache_directory != manifest.cache_key
        and not cache_directory.startswith(f".{manifest.cache_key}.partial-")
    ):
        raise CodeQLDatabaseError("CodeQL database cache directory mismatch")
    marker = database / "codeql-database.yml"
    if not marker.is_file():
        raise CodeQLDatabaseError(f"CodeQL database marker is missing: {marker}")
    marker_digest = _sha256_bytes(marker.read_bytes())
    if marker_digest != manifest.database_marker_sha256:
        raise CodeQLDatabaseError("CodeQL database marker digest mismatch")
    expected_database = _sha256_bytes(
        _canonical_bytes(
            {
                "cache_key": manifest.cache_key,
                "database_info": manifest.database_info,
                "database_marker_sha256": marker_digest,
            }
        )
    )
    if manifest.database_fingerprint != expected_database:
        raise CodeQLDatabaseError("CodeQL database_fingerprint mismatch")


def build_script_text(request: PreparationRequest, cache_key: str) -> str:
    out_dir = request.cache_root / "build-out" / cache_key
    lunch = f"{request.product}-{request.variant}"
    targets = " ".join(shlex.quote(item) for item in request.build_targets)
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            f"export OUT_DIR={shlex.quote(str(out_dir))}",
            f"cd {shlex.quote(str(request.aosp_root))}",
            "source build/envsetup.sh",
            f"lunch {shlex.quote(lunch)}",
            f"m -j{request.threads} {targets}",
            "",
        )
    )


def build_codeql_create_command(
    request: PreparationRequest,
    *,
    cache_key: str,
    database: Path,
    build_script: Path,
) -> list[str]:
    del cache_key
    return [
        str(request.codeql_bin),
        "database",
        "create",
        str(database),
        "--language=java-kotlin",
        "--source-root",
        str(request.aosp_root),
        "--threads",
        str(request.threads),
        "--ram",
        str(request.ram_mb),
        "--command",
        f"bash {shlex.quote(str(build_script))}",
    ]


def load_database_manifest(path: Path) -> CodeQLDatabaseManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CodeQLDatabaseError(f"cannot read CodeQL manifest {path}: {error}") from error
    if not isinstance(payload, dict):
        raise CodeQLDatabaseError("CodeQL manifest must be an object")
    return CodeQLDatabaseManifest.from_dict(payload)


def validate_database_manifest(
    manifest: CodeQLDatabaseManifest,
    *,
    expected_request: PreparationRequest,
    database: Path | None = None,
) -> None:
    expected = {
        "schema_version": 2,
        "status": "verified",
        "language": "java-kotlin",
        "source_fingerprint": expected_request.source_fingerprint,
        "cache_key": preparation_fingerprint(expected_request),
        "product": expected_request.product,
        "variant": expected_request.variant,
        "build_targets": expected_request.build_targets,
        "threads": expected_request.threads,
        "ram_mb": expected_request.ram_mb,
        "codeql_version": expected_request.codeql_version,
        "extractor_version": expected_request.extractor_version,
        "repositories": _sorted_repositories(expected_request.repositories),
    }
    for field, value in expected.items():
        actual = getattr(manifest, field)
        if field == "repositories":
            actual = _sorted_repositories(actual)
        if actual != value:
            raise CodeQLDatabaseError(
                f"CodeQL manifest {field} mismatch: "
                f"expected={value!r} actual={getattr(manifest, field)!r}"
            )
    for field in (
        "database_fingerprint",
        "database_marker_sha256",
        "source_fingerprint",
        "cache_key",
    ):
        _require_digest(field, str(getattr(manifest, field)))
    if manifest.observed_java_files < 0 or manifest.observed_kotlin_files < 0:
        raise CodeQLDatabaseError("observed source counts must not be negative")
    if database is not None:
        validate_manifest_self_consistency(manifest, database=database)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _observed_source_counts(database: Path) -> tuple[int, int]:
    archives = tuple(database.glob("src*.zip"))
    if not archives:
        return 0, 0
    java: set[str] = set()
    kotlin: set[str] = set()
    for archive in archives:
        try:
            with zipfile.ZipFile(archive) as source_zip:
                for name in source_zip.namelist():
                    lowered = name.lower()
                    if lowered.endswith(".java"):
                        java.add(name)
                    elif lowered.endswith((".kt", ".kts")):
                        kotlin.add(name)
        except (OSError, zipfile.BadZipFile) as error:
            raise CodeQLDatabaseError(
                f"cannot inspect CodeQL source archive {archive}: {error}"
            ) from error
    return len(java), len(kotlin)


def _run(
    runner: Runner,
    command: list[str],
    *,
    cwd: Path,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(command, cwd=cwd)
    except subprocess.CalledProcessError as error:
        details: list[str] = []
        for label, value in (("stderr", error.stderr), ("stdout", error.stdout)):
            if value:
                rendered = (
                    value.decode(errors="replace")
                    if isinstance(value, bytes)
                    else str(value)
                ).strip()
                if rendered:
                    details.append(f"{label}:\n{rendered[-12000:]}")
        suffix = "\n" + "\n".join(details) if details else ""
        raise CodeQLDatabaseError(
            f"CodeQL {operation} failed with status {error.returncode}: "
            f"{error}{suffix}"
        ) from error
    except OSError as error:
        raise CodeQLDatabaseError(f"CodeQL {operation} failed: {error}") from error
    if result.returncode != 0:
        raise CodeQLDatabaseError(
            f"CodeQL {operation} failed with status {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return result


def prepare_database(
    request: PreparationRequest,
    *,
    runner: Runner,
) -> Path:
    cache_key = preparation_fingerprint(request)
    databases_root = request.cache_root / "databases"
    entry = databases_root / cache_key
    database = entry / "database"
    manifest_path = entry / "manifest.json"
    if entry.exists():
        manifest = load_database_manifest(manifest_path)
        validate_database_manifest(
            manifest,
            expected_request=request,
            database=database,
        )
        return database.resolve()

    build_entrypoint = request.aosp_root / "build" / "envsetup.sh"
    if not build_entrypoint.is_file():
        raise CodeQLDatabaseError(
            "AOSP build entrypoint is missing: "
            f"{build_entrypoint}. A Java/Kotlin CodeQL database requires a "
            "buildable AOSP checkout; build-mode=none is not accepted because "
            "it excludes Kotlin."
        )

    databases_root.mkdir(parents=True, exist_ok=True)
    partial = databases_root / f".{cache_key}.partial-{os.getpid()}-{uuid.uuid4().hex}"
    partial.mkdir()
    partial_database = partial / "database"
    build_script = partial / "trace-build.sh"
    try:
        build_script.write_text(
            build_script_text(request, cache_key),
            encoding="utf-8",
            newline="\n",
        )
        build_script.chmod(0o700)
        _run(
            runner,
            build_codeql_create_command(
                request,
                cache_key=cache_key,
                database=partial_database,
                build_script=build_script,
            ),
            cwd=request.aosp_root,
            operation="database create",
        )
        marker = partial_database / "codeql-database.yml"
        if not marker.is_file():
            raise CodeQLDatabaseError(
                f"CodeQL database create produced no marker: {marker}"
            )
        info_result = _run(
            runner,
            [
                str(request.codeql_bin),
                "database",
                "info",
                "--format=json",
                str(partial_database),
            ],
            cwd=request.aosp_root,
            operation="database info",
        )
        try:
            database_info = json.loads(info_result.stdout)
        except json.JSONDecodeError as error:
            raise CodeQLDatabaseError(
                f"CodeQL database info returned invalid JSON: {error}"
            ) from error
        if not isinstance(database_info, dict):
            raise CodeQLDatabaseError("CodeQL database info must be an object")
        marker_digest = _sha256_bytes(marker.read_bytes())
        database_fingerprint = _sha256_bytes(
            _canonical_bytes(
                {
                    "cache_key": cache_key,
                    "database_info": database_info,
                    "database_marker_sha256": marker_digest,
                }
            )
        )
        observed_java, observed_kotlin = _observed_source_counts(partial_database)
        manifest = CodeQLDatabaseManifest(
            schema_version=2,
            status="verified",
            cache_key=cache_key,
            language="java-kotlin",
            source_fingerprint=request.source_fingerprint,
            product=request.product,
            variant=request.variant,
            build_targets=request.build_targets,
            threads=request.threads,
            ram_mb=request.ram_mb,
            codeql_version=request.codeql_version,
            extractor_version=request.extractor_version,
            database_fingerprint=database_fingerprint,
            database_marker_sha256=marker_digest,
            observed_java_files=observed_java,
            observed_kotlin_files=observed_kotlin,
            repositories=request.repositories,
            created_at=datetime.now(timezone.utc).isoformat(),
            database_info=database_info,
        )
        _atomic_json(partial / "manifest.json", manifest.to_dict())
        validate_database_manifest(
            manifest,
            expected_request=request,
            database=partial_database,
        )
        try:
            os.replace(partial, entry)
        except FileExistsError:
            winner = load_database_manifest(manifest_path)
            validate_database_manifest(
                winner,
                expected_request=request,
                database=database,
            )
        return database.resolve()
    except BaseException:
        if partial.exists():
            shutil.rmtree(partial)
        raise
    finally:
        if partial.exists():
            shutil.rmtree(partial)
