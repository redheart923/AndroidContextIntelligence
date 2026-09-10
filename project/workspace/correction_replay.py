from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from collectors.codeql.corrections import (
    CorrectionApplication,
    apply_corrections,
    load_corrections,
)


@dataclass(frozen=True)
class CorrectionReplayReport:
    applications: tuple[CorrectionApplication, ...]

    @property
    def applied(self) -> int:
        return sum(item.status == "applied" for item in self.applications)


def replay_graph_corrections(
    database: Path,
    corrections_dir: Path,
    run_ids: Iterable[str],
) -> CorrectionReplayReport:
    corrections = load_corrections(corrections_dir)
    applications: list[CorrectionApplication] = []
    with sqlite3.connect(database) as connection:
        revisions = {
            str(run_id): str(source_fingerprint)
            for run_id, source_fingerprint in connection.execute(
                "SELECT run_id, source_fingerprint FROM extraction_run"
            )
        }
    for run_id in sorted(set(run_ids)):
        if run_id not in revisions:
            raise ValueError(f"unknown extraction run: {run_id}")
        applicable = tuple(
            item
            for item in corrections
            if item.applicable_source_revision == revisions[run_id]
        )
        if not applicable:
            continue
        report = apply_corrections(
            database,
            applicable,
            source_revision=revisions[run_id],
            run_id=run_id,
        )
        applications.extend(report.applications)
    return CorrectionReplayReport(tuple(applications))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay approved graph corrections once")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--corrections-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with sqlite3.connect(args.db) as connection:
            run_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT run_id FROM extraction_run ORDER BY run_id"
                )
            )
        report = replay_graph_corrections(args.db, args.corrections_dir, run_ids)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "applications": [item.__dict__ for item in report.applications],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"ERROR: {error}")
        return 1
    print(f"Graph corrections: {report.applied} applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
