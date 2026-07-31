from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path


DEFAULT_JADX_OPTIONS = (
    "--no-res",
    "--no-debug-info",
    "--threads-count",
    "4",
)
ARTIFACT_SUFFIXES = {".apk", ".jar"}
SOURCE_SUFFIXES = {".java", ".kt"}


class VendorPreparationError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _directory_digest(root: Path) -> tuple[str, int]:
    files = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest(), len(files)


def _ensure_external_input(input_dir: Path, data_root: Path | None) -> None:
    if data_root is None:
        return
    resolved_input = input_dir.resolve()
    resolved_data = data_root.resolve()
    if resolved_input == resolved_data or resolved_data in resolved_input.parents:
        raise VendorPreparationError(
            f"Vendor input must be outside graph data root: {resolved_input}"
        )


def _jadx_identity(jadx: Path) -> dict[str, object]:
    executable = jadx.resolve()
    if not executable.is_file():
        discovered = shutil.which(str(jadx))
        if discovered is None:
            raise VendorPreparationError(f"JADX executable not found: {jadx}")
        executable = Path(discovered).resolve()
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VendorPreparationError(
            f"Cannot inspect JADX executable: {executable}"
        ) from error
    version = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not version:
        raise VendorPreparationError(
            f"Cannot determine JADX version: {executable}"
        )
    return {
        "path": str(executable),
        "version": version.splitlines()[0],
    }


def _cached_record(
    entry: Path,
    expected_key: str,
    artifact_sha256: str,
    jadx: dict[str, object],
    options: tuple[str, ...],
) -> dict[str, object] | None:
    manifest = entry / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    source_dir = entry / "sources"
    if (
        record.get("cache_key") != expected_key
        or record.get("artifact_sha256") != artifact_sha256
        or record.get("jadx") != jadx
        or record.get("options") != list(options)
        or record.get("status") not in {"prepared", "degraded"}
        or not source_dir.is_dir()
    ):
        return None
    output_sha256, count = _directory_digest(source_dir)
    if (
        output_sha256 != record.get("output_sha256")
        or count != record.get("source_file_count")
    ):
        return None
    record["cache_status"] = "reused"
    record["source_dir"] = str(source_dir.resolve())
    return record


def prepare_artifacts(
    input_dir: Path,
    cache_dir: Path,
    jadx: Path,
    *,
    data_root: Path | None = None,
    options: tuple[str, ...] = DEFAULT_JADX_OPTIONS,
) -> dict[str, object]:
    _ensure_external_input(input_dir, data_root)
    artifacts = (
        sorted(
            (
                path
                for path in input_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in ARTIFACT_SUFFIXES
            ),
            key=lambda path: path.relative_to(input_dir).as_posix(),
        )
        if input_dir.is_dir()
        else []
    )
    tool = _jadx_identity(jadx) if artifacts else {
        "path": str(jadx),
        "version": None,
        "status": "unused",
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    summary: Counter[str] = Counter()
    for artifact in artifacts:
        artifact_sha256 = _sha256_file(artifact)
        cache_key = _canonical_digest(
            {
                "artifact_sha256": artifact_sha256,
                "jadx": tool,
                "options": list(options),
            }
        )
        entry = cache_dir / cache_key
        cached = _cached_record(
            entry,
            cache_key,
            artifact_sha256,
            tool,
            options,
        )
        if cached is not None:
            cached.update(
                {
                    "artifact_name": artifact.name,
                    "artifact_path": str(artifact.resolve()),
                    "artifact_size": artifact.stat().st_size,
                    "artifact_sha256": artifact_sha256,
                }
            )
            records.append(cached)
            summary["reused"] += 1
            if cached.get("status") == "degraded":
                summary["degraded"] += 1
            continue

        temporary = cache_dir / f".tmp-{cache_key}-{os.getpid()}"
        if temporary.exists():
            shutil.rmtree(temporary)
        output = temporary / "output"
        output.mkdir(parents=True)
        command = [
            str(tool["path"]),
            "-d",
            str(output),
            *options,
            str(artifact.resolve()),
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            source_dir = output / "sources"
            output_sha256, source_file_count = (
                _directory_digest(source_dir)
                if source_dir.is_dir()
                else (None, 0)
            )
            if result.returncode == 0 and source_file_count:
                status = "prepared"
            elif source_file_count:
                status = "degraded"
            else:
                status = "failed"
            record: dict[str, object] = {
                "artifact_name": artifact.name,
                "artifact_path": str(artifact.resolve()),
                "artifact_sha256": artifact_sha256,
                "artifact_size": artifact.stat().st_size,
                "cache_key": cache_key,
                "cache_status": "created" if status != "failed" else "not_cached",
                "jadx": tool,
                "options": list(options),
                "command": command,
                "exit_status": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "status": status,
                "output_sha256": output_sha256,
                "source_file_count": source_file_count,
                "source_dir": None,
            }
            if status != "failed":
                if entry.exists():
                    shutil.rmtree(entry)
                os.replace(output, entry)
                record["source_dir"] = str((entry / "sources").resolve())
                (entry / "manifest.json").write_text(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            records.append(record)
            summary[status] += 1
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return {
        "schema_version": "1.0",
        "input_dir": str(input_dir.resolve()),
        "cache_dir": str(cache_dir.resolve()),
        "jadx": tool,
        "artifacts": records,
        "summary": {
            key: summary.get(key, 0)
            for key in ("prepared", "reused", "degraded", "failed")
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Vendor APK/JAR sources")
    parser.add_argument("command", choices=["prepare"])
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--jadx", type=Path, default=Path("jadx"))
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    report = prepare_artifacts(
        parsed.input_dir,
        parsed.cache_dir,
        parsed.jadx,
        data_root=parsed.data_root,
    )
    parsed.report.parent.mkdir(parents=True, exist_ok=True)
    parsed.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
