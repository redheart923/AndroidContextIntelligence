from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.project_payload import (  # noqa: E402
    DEFAULT_MANIFEST_NAME,
    PAYLOAD_DIRECTORIES,
    PAYLOAD_FILES,
    PayloadDiff,
    PayloadManifestError,
    compare_payload,
    iter_payload_files,
    load_manifest,
    verify_manifest,
    write_manifest,
)


PRESERVED_RUNTIME_DIRECTORIES = ("data", ".venv")
PRESERVED_LOCAL_FILES = (
    "config/source_roots.toml",
    "configs/local.yaml",
)


class InstallationError(RuntimeError):
    pass


def _replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _temporary_path(target: Path, kind: str) -> Path:
    return target.parent / f".install-{kind}-{target.name}-{uuid4().hex}"


def _validate_source(source: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise InstallationError(f"payload source is not a directory: {source}")

    missing: list[str] = []
    for filename in PAYLOAD_FILES:
        path = source / filename
        if not path.is_file() or path.is_symlink():
            missing.append(filename)
    for directory in PAYLOAD_DIRECTORIES:
        path = source / directory
        if not path.is_dir() or path.is_symlink():
            missing.append(directory)
    if missing:
        raise InstallationError(f"missing payload entries: {', '.join(missing)}")


def _copy_payload(source: Path, stage: Path) -> None:
    stage.mkdir(parents=False)
    for directory in PAYLOAD_DIRECTORIES:
        (stage / directory).mkdir(parents=True, exist_ok=True)
    for path in iter_payload_files(source):
        relative_path = path.relative_to(source)
        destination = stage / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _format_diff(diff: PayloadDiff) -> str:
    details = []
    for category in ("added", "removed", "modified"):
        values = getattr(diff, category)
        if values:
            details.append(f"{category}={','.join(values)}")
    return "; ".join(details)


def _copy_preserved_local_files(target: Path, stage: Path) -> None:
    for relative_name in PRESERVED_LOCAL_FILES:
        source = target / relative_name
        if not source.exists() and not source.is_symlink():
            continue
        if not source.is_file() or source.is_symlink():
            raise InstallationError(
                f"preserved local path must be a regular file: {source}"
            )
        destination = stage / relative_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _create_verified_stage(
    source: Path,
    target: Path,
    source_commit: str,
    preserve_from: Path | None = None,
) -> Path:
    _validate_source(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = _temporary_path(target, "staging")
    try:
        _copy_payload(source, stage)
        copied_diff = compare_payload(source, stage)
        if not copied_diff.is_clean:
            raise InstallationError(
                f"staged payload differs from source: {_format_diff(copied_diff)}"
            )
        if preserve_from is not None:
            _copy_preserved_local_files(preserve_from, stage)
        manifest_path = stage / DEFAULT_MANIFEST_NAME
        write_manifest(stage, manifest_path, source_commit)
        manifest_diff = verify_manifest(stage, load_manifest(manifest_path))
        if not manifest_diff.is_clean:
            raise InstallationError(
                f"staged payload manifest mismatch: {_format_diff(manifest_diff)}"
            )
        return stage
    except Exception:
        _remove_path(stage)
        raise


def _move_runtime_directories(source_root: Path, target_root: Path) -> None:
    for relative_name in PRESERVED_RUNTIME_DIRECTORIES:
        source = source_root / relative_name
        if not source.exists() and not source.is_symlink():
            continue
        if not source.is_dir() or source.is_symlink():
            raise InstallationError(
                f"preserved runtime path must be a regular directory: {source}"
            )
        destination = target_root / relative_name
        _remove_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


def _return_runtime_directories(source_root: Path, target_root: Path) -> None:
    for relative_name in PRESERVED_RUNTIME_DIRECTORIES:
        source = source_root / relative_name
        destination = target_root / relative_name
        if (source.exists() or source.is_symlink()) and not (
            destination.exists() or destination.is_symlink()
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))


def install_fresh(source: Path, target: Path, source_commit: str) -> None:
    source = source.resolve()
    target = target.resolve()
    if target.exists() or target.is_symlink():
        raise InstallationError(f"target already exists: {target}")

    stage = _create_verified_stage(source, target, source_commit)
    try:
        _replace(stage, target)
    except OSError as error:
        _remove_path(stage)
        raise InstallationError(f"fresh installation promotion failed: {error}") from error


def install_upgrade(source: Path, target: Path, source_commit: str) -> Path:
    source = source.resolve()
    target = target.resolve()
    if not target.is_dir() or target.is_symlink():
        raise InstallationError(f"upgrade target is not a directory: {target}")

    stage = _create_verified_stage(
        source,
        target,
        source_commit,
        preserve_from=target,
    )
    rollback = _temporary_path(target, "rollback")
    promoted = False
    try:
        _replace(target, rollback)
        _replace(stage, target)
        promoted = True
        _move_runtime_directories(rollback, target)
        return rollback
    except Exception as error:
        restoration_error: Exception | None = None
        try:
            if promoted and target.exists():
                _return_runtime_directories(target, rollback)
                _remove_path(target)
            if rollback.exists() and not target.exists():
                _replace(rollback, target)
        except Exception as restore_error:
            restoration_error = restore_error
        finally:
            _remove_path(stage)

        message = f"upgrade promotion failed: {error}"
        if restoration_error is not None:
            message += f"; rollback failed: {restoration_error}"
        raise InstallationError(message) from error


def _resolve_source_commit(source: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    environment_value = os.environ.get("ANDROID_CONTEXT_SOURCE_COMMIT")
    if environment_value:
        return environment_value
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "unversioned"


def _print_verification(target: Path) -> int:
    manifest = load_manifest(target / DEFAULT_MANIFEST_NAME)
    diff = verify_manifest(target, manifest)
    if diff.is_clean:
        print(f"payload verification: PASS ({len(manifest.files)} managed files)")
        return 0
    print("payload verification: FAIL")
    for category in ("added", "removed", "modified"):
        for path in getattr(diff, category):
            print(f"{category}: {path}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the canonical Android Context Intelligence project.",
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fresh", action="store_true")
    modes.add_argument("--upgrade", action="store_true")
    modes.add_argument("--verify-only", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source-commit")
    return parser


def main(arguments: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        if args.verify_only:
            return _print_verification(args.target.resolve())
        if args.source is None:
            parser.error("--source is required for --fresh and --upgrade")
        source_commit = _resolve_source_commit(args.source.resolve(), args.source_commit)
        if args.fresh:
            install_fresh(args.source, args.target, source_commit)
            print(f"fresh installation: PASS ({args.target.resolve()})")
        else:
            rollback = install_upgrade(args.source, args.target, source_commit)
            print(f"upgrade installation: PASS ({args.target.resolve()})")
            print(f"rollback source retained at: {rollback}")
        return 0
    except (InstallationError, PayloadManifestError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
