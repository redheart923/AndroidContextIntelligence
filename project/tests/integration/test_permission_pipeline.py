from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from graph.writer import GraphWriter, Node
from workspace.multi_permission import main


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_database(path: Path, source_paths: dict[str, tuple[str, int, int]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )
    writer = GraphWriter(path)
    for node_id, (source_path, line_start, line_end) in source_paths.items():
        writer.upsert_node(
            Node(
                node_id=node_id,
                node_type="KOTLIN_METHOD" if node_id.startswith("KOTLIN") else "JAVA_METHOD",
                display_name="method",
                source_path=source_path,
                line_start=line_start,
                line_end=line_end,
            )
        )
    writer.close()


def write_fixture(root: Path, reverse: bool) -> tuple[dict, dict[str, tuple[str, int, int]]]:
    files = {
        "frameworks/base/AndroidManifest.xml": '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example">
<permission android:name="com.example.LOCAL" android:protectionLevel="signature" />
<uses-permission android:name="android.permission.CAMERA" />
</manifest>''',
        "frameworks/base/Service.java": '''import android.Manifest;
class Service {
  void run() {
    checkCallingPermission(Manifest.permission.CAMERA);
  }
}''',
        "vendor/demo/privapp-permissions-demo.xml": '''<permissions><privapp-permissions package="com.example">
<permission name="android.permission.CAMERA" />
</privapp-permissions></permissions>''',
        "vendor/demo/default-permissions-demo.xml": '''<exceptions><exception package="com.example">
<permission name="android.permission.CAMERA" fixed="true" />
</exception></exceptions>''',
        "vendor/demo/Service.kt": '''class Service {
  fun run() {
    enforcePermission("android.permission.CAMERA", 1, 2, "message")
  }
}''',
    }
    items = list(files.items())
    if reverse:
        items.reverse()
    for relative, text in items:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    revisions = {"frameworks/base": "a" * 40, "vendor/demo": "b" * 40}
    repositories = [
        {
            "name": name,
            "path": name,
            "enabled": True,
            "status": "available",
            "include": [],
            "exclude": [],
            "revision": revision,
        }
        for name, revision in revisions.items()
    ]
    tasks = []
    for repository in repositories:
        languages = ("xml", "java") if repository["name"] == "frameworks/base" else ("xml", "kotlin")
        for language in languages:
            tasks.append(
                {
                    "repository": repository["name"],
                    "repository_path": repository["path"],
                    "language": language,
                    "capability": "permission_semantics",
                    "parser": "test",
                    "status": "scheduled",
                    "files": 1,
                }
            )
    plan = {
        "aosp_root": str(root),
        "default_exclude": [],
        "strict": False,
        "strict_capability": None,
        "repositories": list(reversed(repositories)) if reverse else repositories,
        "tasks": list(reversed(tasks)) if reverse else tasks,
    }
    methods = {
        "JAVA_METHOD:Service#run()": ("frameworks/base/Service.java", 3, 5),
        "KOTLIN_METHOD:Service#run": ("vendor/demo/Service.kt", 2, 4),
    }
    return plan, methods


def run_pipeline(tmp_path: Path, reverse: bool) -> tuple[dict, list[tuple]]:
    aosp = tmp_path / "aosp"
    plan, methods = write_fixture(aosp, reverse)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    database = tmp_path / "graph.db"
    create_database(database, methods)
    report_path = tmp_path / "permission-report.json"

    assert main(["--plan", str(plan_path), "--db", str(database), "--report", str(report_path)]) == 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        projection = connection.execute(
            """
            SELECT edge_type, from_node_id, to_node_id, properties_json,
                   source_path, line_start, line_end, source_revision
            FROM edge
            WHERE edge_type IN (
              'DECLARES_PERMISSION', 'REQUESTS_PERMISSION',
              'ALLOWLISTS_PRIVILEGED_PERMISSION', 'DENIES_PRIVILEGED_PERMISSION',
              'DEFAULT_GRANTS_PERMISSION', 'REQUIRES_PERMISSION',
              'CHECKS_PERMISSION', 'ENFORCES_PERMISSION'
            )
            ORDER BY edge_type, from_node_id, to_node_id, properties_json
            """
        ).fetchall()
    return report, projection


def test_two_repository_pipeline_is_complete_and_deterministic(tmp_path: Path) -> None:
    forward_report, forward_projection = run_pipeline(tmp_path / "forward", False)
    reverse_report, reverse_projection = run_pipeline(tmp_path / "reverse", True)

    assert forward_report["facts_and_edges_by_type"]["REQUESTS_PERMISSION"] >= 1
    assert forward_report["facts_and_edges_by_type"]["CHECKS_PERMISSION"] == 1
    assert forward_report["facts_and_edges_by_type"]["ENFORCES_PERMISSION"] == 1
    assert forward_report["source_revisions"] == {
        "frameworks/base": "a" * 40,
        "vendor/demo": "b" * 40,
    }
    assert forward_report == reverse_report
    assert forward_projection == reverse_projection
