from __future__ import annotations
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from graph.writer import GraphWriter
from workspace.pipeline import load_plan, repositories_for, scan_paths, source_allowed
from collectors.permission.xml_permission_importer import extract_permissions
from collectors.permission.java_permission_scanner import load_methods, scan_file_for_permissions

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    plan = load_plan(args.plan)
    root = Path(plan["aosp_root"])
    defaults = plan.get("default_exclude", [])
    
    writer = GraphWriter(args.db)
    
    xml_files = set()
    for repo in repositories_for(plan, "xml", "permission_declaration"):
        for path in scan_paths(root, repo):
            if path.is_file():
                if path.suffix == '.xml' and source_allowed(root, repo, path, defaults):
                    xml_files.add(path)
            elif path.is_dir():
                for file_path in path.rglob("*.xml"):
                    if file_path.is_file() and source_allowed(root, repo, file_path, defaults):
                        xml_files.add(file_path)

    java_kt_files = set()
    for lang in ["java", "kotlin"]:
        for repo in repositories_for(plan, lang, "permission_enforcement"):
            for path in scan_paths(root, repo):
                if path.is_file():
                    if path.suffix in ('.java', '.kt') and source_allowed(root, repo, path, defaults):
                        java_kt_files.add(path)
                elif path.is_dir():
                    for ext in ["*.java", "*.kt"]:
                        for file_path in path.rglob(ext):
                            if file_path.is_file() and source_allowed(root, repo, file_path, defaults):
                                java_kt_files.add(file_path)

    try:
        nodes = []
        edges = []

        # Process XMLs
        for xml_path in sorted(xml_files):
            results = extract_permissions(xml_path, root)
            for node, edge in results:
                writer.upsert_node(node)
                writer.upsert_edge(edge)
                nodes.append(node)
                edges.append(edge)

        # Process Java/Kt
        with sqlite3.connect(args.db) as conn:
            for file_path in sorted(java_kt_files):
                try:
                    relative_path = str(file_path.relative_to(root)).replace("\\", "/")
                except ValueError:
                    relative_path = str(file_path).replace("\\", "/")
                
                methods = load_methods(conn, relative_path)
                file_edges = scan_file_for_permissions(file_path, root, methods)
                for edge in file_edges:
                    writer.upsert_edge(edge)
                    edges.append(edge)
                    
    finally:
        writer.close()

    summary = defaultdict(int)
    for edge in edges:
        summary[edge.edge_type] += 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps({
            "summary": dict(sorted(summary.items())),
            "nodes_count": len(nodes),
            "edges_count": len(edges)
        }, indent=2),
        encoding="utf-8"
    )
    
    print(f"Permission graph: extracted {len(nodes)} declarations, found {len(edges)} enforcements")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
