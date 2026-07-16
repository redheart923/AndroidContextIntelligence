from __future__ import annotations
import argparse, json, os, tempfile
from pathlib import Path
from .planner import CoverageError, build_workspace_plan


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--strict-capability")
    args = parser.parse_args()
    try:
        plan = build_workspace_plan(args.config, args.registry, args.strict, args.strict_capability)
    except CoverageError as error:
        print(f"ERROR: {error}")
        return 4
    value = plan.to_dict()
    atomic_json(args.out_dir / "repositories.json", value["repositories"])
    atomic_json(args.out_dir / "language-inventory.json", value["inventories"])
    atomic_json(args.out_dir / "execution-plan.json", value)
    coverage = [{"repository": x["repository"], "language": x["language"],
                 "capability": x["capability"], "parser": x["parser"],
                 "status": x["status"], "files": x["files"]} for x in value["tasks"]]
    atomic_json(args.out_dir / "capability-report.json", coverage)
    enabled = sum(1 for x in value["repositories"] if x["enabled"])
    unsupported = sum(1 for x in coverage if x["status"] == "unsupported")
    print(f"Repositories discovered: {len(value['repositories'])}; enabled: {enabled}; unsupported capability entries: {unsupported}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
