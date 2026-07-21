from __future__ import annotations
import json, sqlite3, subprocess, sys
from pathlib import Path

from workspace.cli import atomic_json
from workspace.pipeline import load_plan, run_java, run_inheritance
from workspace.planner import build_workspace_plan


def test_two_repositories_flow_through_all_installed_graph_layers(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    aosp = tmp_path / "aosp"
    base = aosp / "frameworks/base"
    vendor = aosp / "vendor/demo"
    base.mkdir(parents=True); vendor.mkdir(parents=True)
    (base / "Base.java").write_text("package common; public class Base {}", encoding="utf-8")
    (base / "IDemo.aidl").write_text("package demo; interface IDemo { void ping(); }", encoding="utf-8")
    (base / "DemoService.java").write_text("package demo; public class DemoService extends IDemo.Stub {}", encoding="utf-8")
    (vendor / "Child.java").write_text('''
package vendor.demo;
import common.Base;
public class Child extends Base {}
class Registrar { void register() { ServiceManager.addService("vendor.demo", new Child()); } }
''', encoding="utf-8")
    config = tmp_path / "roots.toml"
    config.write_text(f'''
[workspace]
aosp_root = "{aosp}"
auto_discover_manifest = false
[repositories."frameworks/base"]
enabled = true
[repositories."vendor/demo"]
enabled = true
''', encoding="utf-8")
    registry = project_root / "config/parser_registry.toml"
    plan = build_workspace_plan(config, registry)
    plan_path = tmp_path / "execution-plan.json"
    atomic_json(plan_path, plan.to_dict())
    db = tmp_path / "graph.db"
    with sqlite3.connect(db) as connection:
        connection.executescript((project_root / "storage/schema.sql").read_text(encoding="utf-8"))
    ctags_dir = tmp_path / "ctags"
    run_java(load_plan(plan_path), db, ctags_dir)
    subprocess.run([sys.executable, "-m", "workspace.multi_aidl", "--plan", str(plan_path),
        "--db", str(db), "--report", str(tmp_path / "aidl.json")], check=True, cwd=project_root)
    run_inheritance(plan_path, load_plan(plan_path), db, ctags_dir, tmp_path / "inheritance")
    subprocess.run([sys.executable, "-m", "workspace.multi_service", "--plan", str(plan_path),
        "--db", str(db), "--report", str(tmp_path / "service.json")], check=True, cwd=project_root)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT 1 FROM edge WHERE edge_type='IMPLEMENTS_BINDER'").fetchone()
        assert connection.execute("SELECT 1 FROM edge WHERE edge_type='EXTENDS' AND source_path LIKE 'vendor/demo/%'").fetchone()
        assert connection.execute("SELECT 1 FROM node WHERE node_type='BINDER_SERVICE_NAME' AND qualified_name='vendor.demo'").fetchone()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
