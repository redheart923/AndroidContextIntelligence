from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from collectors.codeql.decode import decode_csv
from collectors.codeql.model import record_to_dict


Runner = Callable[..., subprocess.CompletedProcess[str]]


class CodeQLRunnerError(RuntimeError):
    pass


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def query_result_key(
    database_fingerprint: str,
    pack_lock_hash: str,
    query_id: str,
    query_version: str,
    parameters: dict[str, object],
) -> str:
    return _sha256_bytes(
        _canonical_bytes(
            {
                "database_fingerprint": database_fingerprint,
                "pack_lock_hash": pack_lock_hash,
                "query_id": query_id,
                "query_version": query_version,
                "parameters": parameters,
            }
        )
    )


@dataclass(frozen=True)
class QueryArtifact:
    query_id: str
    query_version: str
    cache_key: str
    cache_status: str
    row_count: int
    raw_path: str
    csv_path: str
    normalized_path: str
    raw_sha256: str
    csv_sha256: str
    normalized_sha256: str


@dataclass(frozen=True)
class QueryRunManifest:
    schema_version: int
    database_fingerprint: str
    pack_lock_sha256: str
    codeql_version: str
    created_at: str
    queries: tuple[QueryArtifact, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["queries"] = [asdict(query) for query in self.queries]
        return value


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _database_fingerprint(database: Path) -> str:
    manifest = database.parent / "manifest.json"
    if manifest.is_file():
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
            fingerprint = str(value["database_fingerprint"])
            if len(fingerprint) == 64:
                return fingerprint
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            pass
    marker = database / "codeql-database.yml"
    if not marker.is_file():
        raise CodeQLRunnerError(f"CodeQL database marker is missing: {marker}")
    return _sha256_file(marker)


def _run(runner: Runner, command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(command, cwd=cwd, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise CodeQLRunnerError(f"CodeQL command failed: {error}") from error
    if result.returncode != 0:
        raise CodeQLRunnerError(
            f"CodeQL command failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


def _load_artifact(path: Path) -> QueryArtifact:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return QueryArtifact(**value)
    except (OSError, TypeError, json.JSONDecodeError) as error:
        raise CodeQLRunnerError(f"invalid query cache manifest {path}: {error}") from error


def _valid_artifact(artifact: QueryArtifact, cache_key: str) -> bool:
    if artifact.cache_key != cache_key:
        return False
    for path_text, digest in (
        (artifact.raw_path, artifact.raw_sha256),
        (artifact.csv_path, artifact.csv_sha256),
        (artifact.normalized_path, artifact.normalized_sha256),
    ):
        path = Path(path_text)
        if not path.is_file() or _sha256_file(path) != digest:
            return False
    return True


def _codeql_version(codeql_bin: Path, runner: Runner, cwd: Path) -> str:
    result = _run(runner, [str(codeql_bin), "version", "--format=json"], cwd)
    try:
        value = json.loads(result.stdout)
        return str(value["version"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise CodeQLRunnerError(f"invalid CodeQL version JSON: {error}") from error


def _publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def run_queries(
    database: Path,
    pack: Path,
    output_dir: Path,
    codeql_bin: Path,
    *,
    runner: Runner = subprocess.run,
) -> QueryRunManifest:
    database = database.resolve()
    pack = pack.resolve()
    output_dir = output_dir.resolve()
    lock = pack / "codeql-pack.lock.yml"
    if not lock.is_file():
        raise CodeQLRunnerError(f"CodeQL pack lock is missing: {lock}")
    queries = tuple(sorted((pack / "queries").glob("*.ql")))
    if not queries:
        raise CodeQLRunnerError(f"CodeQL pack has no queries: {pack}")
    database_fingerprint = _database_fingerprint(database)
    lock_hash = _sha256_file(lock)
    version = _codeql_version(codeql_bin, runner, pack)
    artifacts: list[QueryArtifact] = []
    for query in queries:
        query_id = query.stem
        query_version = "1"
        cache_key = query_result_key(
            database_fingerprint, lock_hash, query_id, query_version, {}
        )
        cache = output_dir / "cache" / cache_key
        cache_manifest = cache / "manifest.json"
        artifact: QueryArtifact | None = None
        if cache_manifest.is_file():
            try:
                candidate = _load_artifact(cache_manifest)
                if _valid_artifact(candidate, cache_key):
                    artifact = replace(candidate, cache_status="hit")
            except CodeQLRunnerError:
                artifact = None
        if artifact is None:
            partial = output_dir / f".{query_id}.{uuid.uuid4().hex}.partial"
            partial.mkdir(parents=True, exist_ok=False)
            try:
                bqrs = partial / f"{query_id}.bqrs"
                csv_path = partial / f"{query_id}.csv"
                normalized = partial / f"{query_id}.jsonl"
                _run(
                    runner,
                    [str(codeql_bin), "query", "run", "--database", str(database),
                     "--output", str(bqrs), str(query)],
                    pack,
                )
                _run(
                    runner,
                    [str(codeql_bin), "bqrs", "decode", "--format=csv", "--entities=all",
                     "--output", str(csv_path), str(bqrs)],
                    pack,
                )
                records = decode_csv(
                    query_id, csv_path.read_text(encoding="utf-8"),
                    query_version=query_version,
                    database_fingerprint=database_fingerprint,
                )
                normalized_text = "".join(
                    json.dumps(record_to_dict(record), ensure_ascii=False, sort_keys=True) + "\n"
                    for record in records
                )
                _atomic_text(normalized, normalized_text)
                cache.parent.mkdir(parents=True, exist_ok=True)
                if cache.exists():
                    shutil.rmtree(cache)
                os.replace(partial, cache)
                artifact = QueryArtifact(
                    query_id=query_id, query_version=query_version, cache_key=cache_key,
                    cache_status="miss", row_count=len(records),
                    raw_path=str(cache / bqrs.name), csv_path=str(cache / csv_path.name),
                    normalized_path=str(cache / normalized.name),
                    raw_sha256=_sha256_file(cache / bqrs.name),
                    csv_sha256=_sha256_file(cache / csv_path.name),
                    normalized_sha256=_sha256_file(cache / normalized.name),
                )
                _atomic_json(cache / "manifest.json", asdict(artifact))
            finally:
                if partial.exists():
                    shutil.rmtree(partial)
        _publish(Path(artifact.normalized_path), output_dir / "normalized" / f"{query_id}.jsonl")
        artifacts.append(artifact)
    manifest = QueryRunManifest(
        schema_version=1, database_fingerprint=database_fingerprint,
        pack_lock_sha256=lock_hash, codeql_version=version,
        created_at=datetime.now(timezone.utc).isoformat(), queries=tuple(artifacts),
    )
    _atomic_json(output_dir / "query-run-manifest.json", manifest.to_dict())
    return manifest
