from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GraphDiff:
    counts: dict[str, int]
    details: dict[str, tuple[str, ...]]

    def as_dict(self) -> dict[str, object]:
        return {
            "counts": self.counts,
            "details": {key: list(value) for key, value in self.details.items()},
        }


def _facts(connection: sqlite3.Connection, effective: bool) -> dict[str, str]:
    suffix = "effective_" if effective else ""
    facts: dict[str, str] = {}
    for kind, table, id_column in (
        ("node", suffix + "node", "node_id"),
        ("edge", suffix + "edge", "edge_id"),
    ):
        rows = connection.execute(
            f"SELECT {id_column}, COALESCE(content_hash, '') FROM {table}"
        )
        facts.update({f"{kind}:{identity}": str(digest) for identity, digest in rows})
    return facts


def _targets(connection: sqlite3.Connection, action: str) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT fc.target_fact_uri
            FROM fact_correction fc
            JOIN correction_application ca USING(correction_id)
            WHERE fc.action=? AND ca.application_status='applied'
            """,
            (action,),
        )
    }


def compare_graphs(before: Path, after: Path) -> GraphDiff:
    with sqlite3.connect(before) as previous, sqlite3.connect(after) as current:
        before_raw = _facts(previous, False)
        after_raw = _facts(current, False)
        before_effective = _facts(previous, True)
        after_effective = _facts(current, True)
        suppressed = _targets(current, "suppress")
        superseded = _targets(current, "replace")
        stale = {
            f"correction:{row[0]}"
            for row in current.execute(
                "SELECT correction_id FROM correction_application WHERE application_status='stale'"
            )
        }
    common_effective = before_effective.keys() & after_effective.keys()
    changed = {
        uri
        for uri in common_effective
        if before_effective[uri] != after_effective[uri]
    }
    removed = set(before_effective) - set(after_effective) - suppressed - superseded
    added = set(after_effective) - set(before_effective)
    # Reviewed replacement facts are an addition, while their raw targets stay auditable.
    suppressed &= set(before_raw) & set(after_raw)
    superseded &= set(before_raw) & set(after_raw)
    details = {
        "added": tuple(sorted(added)),
        "removed": tuple(sorted(removed)),
        "changed": tuple(sorted(changed)),
        "suppressed": tuple(sorted(suppressed)),
        "superseded": tuple(sorted(superseded)),
        "stale": tuple(sorted(stale)),
    }
    return GraphDiff(
        counts={key: len(value) for key, value in details.items()},
        details=details,
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two semantic graph databases")
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(arguments)
    diff = compare_graphs(args.before, args.after)
    if args.format == "json":
        print(json.dumps(diff.as_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    else:
        for classification, count in diff.counts.items():
            print(f"{classification}: {count}")
            for uri in diff.details[classification]:
                print(f"  {uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
