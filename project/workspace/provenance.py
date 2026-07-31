from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from .revisions import inspect_repository_provenance


class ProvenanceError(RuntimeError):
    pass


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def provenance_fingerprint(payload: object) -> str:
    fingerprint_payload = payload
    if isinstance(payload, dict) and "fingerprint" in payload:
        fingerprint_payload = {
            key: value for key, value in payload.items() if key != "fingerprint"
        }
    return hashlib.sha256(_canonical_bytes(fingerprint_payload)).hexdigest()


def _file_identity(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {"path": str(path.resolve()) if path is not None else None, "sha256": None}
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _command_identity(
    executable: str,
    arguments: tuple[str, ...],
    *,
    optional: bool = False,
) -> dict[str, object]:
    path = shutil.which(executable)
    if path is None:
        return {
            "status": "optional_missing" if optional else "missing",
            "path": None,
            "version": None,
        }
    try:
        result = subprocess.run(
            [path, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "failed",
            "path": str(Path(path).resolve()),
            "version": None,
            "error": type(error).__name__,
        }
    output = (result.stdout or result.stderr).strip()
    return {
        "status": "available" if result.returncode == 0 else "failed",
        "path": str(Path(path).resolve()),
        "version": output.splitlines()[0] if output else None,
        "exit_status": result.returncode,
    }


def collect_provenance(
    plan_path: Path,
    source_config: Path,
    parser_registry: Path,
    local_config: Path | None = None,
    vendor_manifest: Path | None = None,
) -> dict[str, object]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    aosp_root = Path(str(plan["aosp_root"]))
    default_exclude = tuple(plan.get("default_exclude", ()))
    repositories: list[dict[str, object]] = []
    for item in plan.get("repositories", []):
        if not item.get("enabled"):
            continue
        repository_path = Path(str(item["path"]))
        location = (
            repository_path
            if repository_path.is_absolute()
            else aosp_root / repository_path
        )
        current = inspect_repository_provenance(
            location,
            tuple(item.get("include", ())),
            default_exclude + tuple(item.get("exclude", ())),
            tuple(item.get("languages", ())),
        )
        record = {
            "name": item["name"],
            "path": item["path"],
            **current.to_dict(),
            "planned_revision": item.get("revision"),
            "planned_inventory_sha256": item.get("inventory_sha256"),
        }
        record["matches_plan"] = (
            record["revision"] == record["planned_revision"]
            and record["inventory_sha256"] == record["planned_inventory_sha256"]
        )
        repositories.append(record)
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "repositories": repositories,
        "configs": {
            "source_roots.default.toml": _file_identity(source_config),
            "source_roots.local.toml": _file_identity(local_config),
            "parser_registry.toml": _file_identity(parser_registry),
        },
        "tools": {
            "python": {
                "status": "available",
                "path": str(Path(sys.executable).resolve()),
                "version": sys.version.split()[0],
            },
            "sqlite": {
                "status": "available",
                "version": sqlite3.sqlite_version,
                "cli": _command_identity("sqlite3", ("--version",)),
            },
            "ctags": _command_identity("ctags", ("--version",)),
            "jadx": _command_identity("jadx", ("--version",), optional=True),
        },
        "vendor_artifacts": _file_identity(vendor_manifest),
    }
    payload["fingerprint"] = provenance_fingerprint(payload)
    return payload


def validate_provenance(
    payload: dict[str, object],
    *,
    require_complete: bool = False,
) -> None:
    gaps: list[str] = []
    fingerprint = payload.get("fingerprint")
    if (
        fingerprint is not None
        and fingerprint != provenance_fingerprint(payload)
    ):
        gaps.append("fingerprint mismatch")
    repositories = payload.get("repositories")
    if not isinstance(repositories, list):
        gaps.append("repositories")
    else:
        for item in repositories:
            if not isinstance(item, dict):
                gaps.append("repository record")
                continue
            name = str(item.get("name", item.get("path", "repository")))
            if item.get("state") == "missing":
                gaps.append(f"{name}:missing repository")
            if not item.get("inventory_sha256"):
                gaps.append(f"{name}:missing inventory")
            if item.get("matches_plan") is False:
                gaps.append(f"{name}:source changed during build")
    configs = payload.get("configs")
    if not isinstance(configs, dict):
        gaps.append("configs")
    else:
        for name, identity in configs.items():
            if (
                name != "source_roots.local.toml"
                and (
                    not isinstance(identity, dict)
                    or not identity.get("sha256")
                )
            ):
                gaps.append(f"{name}:missing digest")
    tools = payload.get("tools")
    if not isinstance(tools, dict):
        gaps.append("tools")
    else:
        for name in ("python", "sqlite", "ctags"):
            identity = tools.get(name)
            if not isinstance(identity, dict) or identity.get("status") != "available":
                gaps.append(f"{name}:missing identity")
    if require_complete and gaps:
        raise ProvenanceError("missing provenance: " + ", ".join(gaps))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and validate build provenance")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--plan", type=Path, required=True)
    collect.add_argument("--source-config", type=Path, required=True)
    collect.add_argument("--local-config", type=Path)
    collect.add_argument("--registry", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--vendor-manifest", type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("--provenance", type=Path, required=True)
    validate.add_argument("--require-complete", action="store_true")
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    if parsed.command == "collect":
        payload = collect_provenance(
            parsed.plan,
            parsed.source_config,
            parsed.registry,
            parsed.local_config,
            parsed.vendor_manifest,
        )
        parsed.output.parent.mkdir(parents=True, exist_ok=True)
        parsed.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    if parsed.command == "validate":
        payload = json.loads(parsed.provenance.read_text(encoding="utf-8"))
        validate_provenance(payload, require_complete=parsed.require_complete)
        return 0
    raise AssertionError(f"unhandled command: {parsed.command}")


if __name__ == "__main__":
    raise SystemExit(main())
