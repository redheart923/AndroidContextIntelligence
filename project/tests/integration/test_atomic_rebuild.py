from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest


SNAPSHOT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SCRIPT = SNAPSHOT_ROOT / "scripts" / "rebuild_all.sh"
BASH = shutil.which("bash")
FLOCK = shutil.which("flock")


def test_canonical_rebuild_declares_atomic_staging_contract() -> None:
    script = CANONICAL_SCRIPT.read_text(encoding="utf-8")

    assert "--keep-failed-db" in script
    assert 'flock -n 9' in script
    assert "workspace.build_publish recover" in script
    assert "workspace.build_publish begin" in script
    assert "workspace.build_publish prepare" in script
    assert "workspace.build_publish publish" in script
    assert 'STAGED_DB="$STAGING/android_context.db"' in script
    assert 'STAGED_WORKSPACE="$STAGING/workspace"' in script
    assert 'STAGED_RAW="$STAGING/raw"' in script


SCHEMA = """
CREATE TABLE node (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  qualified_name TEXT,
  display_name TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL
);
CREATE TABLE edge (
  edge_id TEXT PRIMARY KEY,
  edge_type TEXT NOT NULL,
  from_node_id TEXT NOT NULL,
  to_node_id TEXT NOT NULL,
  properties_json TEXT NOT NULL DEFAULT '{}',
  source_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_revision TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  content_hash TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT NOT NULL,
  FOREIGN KEY(from_node_id) REFERENCES node(node_id),
  FOREIGN KEY(to_node_id) REFERENCES node(node_id)
);
"""


CLI_STUB = r'''from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
parser.add_argument("--registry", required=True)
parser.add_argument("--out-dir", type=Path, required=True)
parser.add_argument("--strict", action="store_true")
parser.add_argument("--strict-capability")
args = parser.parse_args()
args.out_dir.mkdir(parents=True, exist_ok=True)
(args.out_dir / "execution-plan.json").write_text("{}\n", encoding="utf-8")
(args.out_dir / "capability-report.json").write_text(
    json.dumps([{"status": "scheduled"}]) + "\n", encoding="utf-8"
)
(args.out_dir / "marker.txt").write_text("new", encoding="utf-8")
'''


PIPELINE_STUB = r'''from __future__ import annotations
import argparse
import os
from pathlib import Path
from graph.writer import Edge, GraphWriter, Node

parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--plan")
parser.add_argument("--db", type=Path, required=True)
parser.add_argument("--ctags-dir", type=Path)
parser.add_argument("--report-dir", type=Path)
args = parser.parse_args()
if args.command == "java" and os.environ.get("FORCE_IMPORTER_FAILURE") == "1":
    raise SystemExit(17)
if args.ctags_dir:
    args.ctags_dir.mkdir(parents=True, exist_ok=True)
    (args.ctags_dir / "marker.txt").write_text("new", encoding="utf-8")
if args.command == "annotate":
    writer = GraphWriter(args.db)
    writer.upsert_node(Node(
        node_id="JAVA_CLASS:fixture.LocalService",
        node_type="JAVA_CLASS",
        qualified_name="fixture.LocalService",
        display_name="LocalService",
        extractor="fixture",
    ))
    writer.upsert_node(Node(
        node_id="LOCAL_SERVICE_KEY:fixture.LocalKey",
        node_type="LOCAL_SERVICE_KEY",
        qualified_name="fixture.LocalKey",
        display_name="LocalKey",
        extractor="fixture",
    ))
    writer.upsert_edge(Edge(
        edge_type="EXPOSED_AS_LOCAL_SERVICE",
        from_node_id="JAVA_CLASS:fixture.LocalService",
        to_node_id="LOCAL_SERVICE_KEY:fixture.LocalKey",
        extractor="fixture",
    ))
    writer.close()
'''


REPORT_STUB = r'''from __future__ import annotations
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--plan")
parser.add_argument("--db")
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text("{}\n", encoding="utf-8")
'''


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_database(path: Path, build_id: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute(
        """
        INSERT INTO node (
            node_id, node_type, qualified_name, display_name, properties_json,
            source_revision, extractor, extractor_version, content_hash,
            status, updated_at
        ) VALUES (?, 'GRAPH_BUILD', ?, ?, '{}', 'fixture', 'fixture', '1', '',
                  'active', '2026-07-16T00:00:00Z')
        """,
        (f"GRAPH_BUILD:{build_id}", build_id, build_id),
    )
    connection.commit()
    connection.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    if BASH is None or FLOCK is None:
        pytest.skip("atomic rebuild integration requires bash and flock")
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(SNAPSHOT_ROOT / "workspace", root / "workspace")
    shutil.copytree(SNAPSHOT_ROOT / "graph", root / "graph")
    (root / "scripts").mkdir()
    shutil.copy2(CANONICAL_SCRIPT, root / "scripts" / "rebuild_all.sh")
    _write(root / ".venv/bin/activate", "")
    _write(root / "storage/schema.sql", SCHEMA)
    _write(root / "config/source_roots.toml", "[workspace]\n")
    _write(root / "config/parser_registry.toml", "[parsers]\n")
    _write(root / "workspace/cli.py", CLI_STUB)
    _write(root / "workspace/pipeline.py", PIPELINE_STUB)
    _write(root / "workspace/multi_aidl.py", REPORT_STUB)
    _write(root / "workspace/multi_service.py", REPORT_STUB)
    _write(root / "workspace/multi_permission.py", REPORT_STUB)
    data = root / "data"
    data.mkdir()
    _seed_database(data / "android_context.db", "old")
    for name in ("workspace", "raw"):
        _write(data / name / "marker.txt", "old")
    return root


def _run(project: Path, *arguments: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(environment)
    return subprocess.run(
        [BASH, str(project / "scripts/rebuild_all.sh"), *arguments],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_forced_importer_failure_preserves_live_batch(project: Path) -> None:
    database = project / "data/android_context.db"
    before = _checksum(database)

    result = _run(project, FORCE_IMPORTER_FAILURE="1")

    assert result.returncode != 0
    assert _checksum(database) == before
    assert (project / "data/workspace/marker.txt").read_text() == "old"
    assert (project / "data/raw/marker.txt").read_text() == "old"
    staging = project / "data/staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_keep_failed_retains_and_prints_staging_batch(project: Path) -> None:
    result = _run(project, "--keep-failed-db", FORCE_IMPORTER_FAILURE="1")

    assert result.returncode != 0
    retained = [
        Path(line)
        for line in result.stdout.splitlines()
        if "/data/staging/" in line
    ]
    assert len(retained) == 1
    assert retained[0].is_dir()


def test_plan_only_creates_no_staged_database(project: Path) -> None:
    shutil.rmtree(project / "data/workspace")

    result = _run(project, "--plan-only")

    assert result.returncode == 0, result.stderr
    assert (project / "data/workspace/execution-plan.json").is_file()
    assert not (project / "data/staging").exists()


@pytest.mark.parametrize("arguments", [(), ("--plan-only",)])
def test_common_lock_rejects_concurrent_modes(
    project: Path,
    arguments: tuple[str, ...],
) -> None:
    lock = project / "data/.rebuild.lock"
    holder = subprocess.Popen([FLOCK, "-n", str(lock), "sleep", "5"])
    try:
        time.sleep(0.2)
        result = _run(project, *arguments)
    finally:
        holder.terminate()
        holder.wait(timeout=5)

    assert result.returncode != 0
    assert "another rebuild is already running" in result.stderr
    assert (project / "data/workspace/marker.txt").read_text() == "old"


def test_successful_publication_exposes_matching_build_ids(project: Path) -> None:
    result = _run(project)

    assert result.returncode == 0, result.stderr
    database = project / "data/android_context.db"
    connection = sqlite3.connect(database)
    database_build_id = connection.execute(
        "SELECT qualified_name FROM node WHERE node_type='GRAPH_BUILD'"
    ).fetchone()[0]
    connection.close()
    manifest = json.loads(
        (project / "data/workspace/build-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["build_id"] == database_build_id
    assert (project / "data/workspace/marker.txt").read_text() == "new"
    assert (project / "data/raw/ctags/marker.txt").read_text() == "new"
