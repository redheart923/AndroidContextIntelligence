from __future__ import annotations

import argparse
import json
from pathlib import Path

from workspace.codeql_runner import run_queries


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the locked CodeQL pack and normalize its result tables."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codeql-bin", type=Path, required=True)
    args = parser.parse_args()
    manifest = run_queries(
        args.database,
        args.pack,
        args.output,
        args.codeql_bin,
    )
    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
