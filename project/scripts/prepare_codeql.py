from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from workspace.codeql_database import (
    CodeQLDatabaseError,
    PreparationRequest,
    RepositoryIdentity,
    prepare_database as _prepare_database,
)


def _runner(
    command: list[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def prepare_database(
    request: PreparationRequest,
    *,
    runner=_runner,
) -> Path:
    return _prepare_database(request, runner=runner)


def _resolve_codeql(value: str) -> Path:
    candidate = Path(value)
    resolved = candidate if candidate.is_file() else Path(shutil.which(value) or "")
    if not resolved.is_file():
        raise CodeQLDatabaseError(f"CodeQL executable was not found: {value}")
    return resolved.resolve()


def _command_output(codeql: Path, arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            [str(codeql), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise CodeQLDatabaseError(
            f"cannot inspect CodeQL {' '.join(arguments)}: {error}"
        ) from error
    return result.stdout.strip() or result.stderr.strip()


def _version_identity(codeql: Path) -> tuple[str, str]:
    version_output = _command_output(codeql, ["version", "--format=json"])
    try:
        version_document = json.loads(version_output)
    except json.JSONDecodeError:
        version = version_output.splitlines()[0]
    else:
        version = str(
            version_document.get("version")
            or version_document.get("versionNumber")
            or version_output
        )
    languages = _command_output(codeql, ["resolve", "languages", "--format=json"])
    extractor = "resolve-languages:" + hashlib.sha256(
        languages.encode("utf-8")
    ).hexdigest()
    return version, extractor


def _repositories(plan_path: Path) -> tuple[RepositoryIdentity, ...]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CodeQLDatabaseError(f"cannot read workspace plan {plan_path}: {error}") from error
    records: list[RepositoryIdentity] = []
    for item in plan.get("repositories", []):
        if not item.get("enabled") or item.get("status") != "available":
            continue
        revision = item.get("revision")
        inventory = item.get("inventory_sha256")
        file_count = int(item.get("inventory_file_count", 0))
        if not revision or not inventory or file_count < 1:
            raise CodeQLDatabaseError(
                f"repository lacks revision/inventory identity: {item.get('name')}"
            )
        records.append(
            RepositoryIdentity(
                name=str(item["name"]),
                path=str(item["path"]),
                revision=str(revision),
                dirty=bool(item.get("revision_dirty", False)),
                inventory_sha256=str(inventory),
                file_count=file_count,
            )
        )
    if not records:
        raise CodeQLDatabaseError("workspace plan has no enabled repositories")
    return tuple(sorted(records, key=lambda item: item.path))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a verified Java/Kotlin CodeQL database for AOSP"
    )
    parser.add_argument("--aosp-root", type=Path, required=True)
    parser.add_argument("--codeql-bin", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--build-target", action="append", required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--ram-mb", type=int, default=24576)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("data/workspace/execution-plan.json"),
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        codeql = _resolve_codeql(args.codeql_bin)
        codeql_version, extractor_version = _version_identity(codeql)
        request = PreparationRequest(
            aosp_root=args.aosp_root.resolve(),
            codeql_bin=codeql,
            product=args.product,
            variant=args.variant,
            build_targets=tuple(args.build_target),
            threads=args.threads,
            ram_mb=args.ram_mb,
            cache_root=args.cache_root.resolve(),
            repositories=_repositories(args.plan),
            codeql_version=codeql_version,
            extractor_version=extractor_version,
        )
        print(prepare_database(request))
    except CodeQLDatabaseError as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
