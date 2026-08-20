from __future__ import annotations

import json
from pathlib import Path

from tests.integration.test_atomic_rebuild import (
    CANONICAL_SCRIPT,
    _checksum,
    _run,
    project,
)


def test_codeql_import_occurs_before_validation_and_publication() -> None:
    script = CANONICAL_SCRIPT.read_text(encoding="utf-8")

    assert "--codeql-db" in script
    assert "--corrections-dir" in script
    assert "--retain-history" in script
    assert script.index("workspace.codeql_import") < script.index(
        "workspace.call_dataflow_validation"
    )
    assert script.index("workspace.call_dataflow_validation") < script.index(
        "workspace.build_publish prepare"
    )


def test_codeql_validation_failure_preserves_live_database(project: Path) -> None:
    database = project / "data/android_context.db"
    before = _checksum(database)
    fake = project / "fake-codeql-db"
    fake.mkdir()

    result = _run(
        project,
        "--codeql-db",
        str(fake),
        "--codeql-bin",
        "/bin/true",
        FORCE_CODEQL_VALIDATION_FAILURE="1",
    )

    assert result.returncode != 0
    assert _checksum(database) == before
    assert (project / "data/workspace/marker.txt").read_text() == "old"


def test_default_rebuild_without_codeql_still_publishes_base_graph(
    project: Path,
) -> None:
    result = _run(project)

    assert result.returncode == 0, result.stderr
    report = json.loads(
        (project / "data/workspace/capability-report.json").read_text(encoding="utf-8")
    )
    assert any(
        item["capability"] == "call_graph" and item["status"] == "degraded"
        for item in report
    )
    skipped = json.loads(
        (project / "data/raw/codeql/call-dataflow-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert skipped["status"] == "skipped"
