from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.project_payload import (  # noqa: E402
    DEFAULT_MANIFEST_NAME,
    PayloadManifestError,
    load_manifest,
    verify_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an installed Android Context Intelligence payload.",
    )
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    target = args.target.resolve()
    manifest_path = (
        args.manifest.resolve()
        if args.manifest is not None
        else target / DEFAULT_MANIFEST_NAME
    )

    if not target.is_dir():
        print(f"ERROR: target directory does not exist: {target}", file=sys.stderr)
        return 2

    try:
        manifest = load_manifest(manifest_path)
    except PayloadManifestError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    diff = verify_manifest(target, manifest)
    if diff.is_clean:
        print(f"payload verification: PASS ({len(manifest.files)} managed files)")
        return 0

    print("payload verification: FAIL")
    for category in ("added", "removed", "modified"):
        for path in getattr(diff, category):
            print(f"{category}: {path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
