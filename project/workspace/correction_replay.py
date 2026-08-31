from __future__ import annotations

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
        report = apply_corrections(
            database,
            corrections,
            source_revision=revisions[run_id],
            run_id=run_id,
        )
        applications.extend(report.applications)
    return CorrectionReplayReport(tuple(applications))
