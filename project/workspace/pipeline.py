from __future__ import annotations
import argparse, json, sqlite3, subprocess, sys
from pathlib import Path


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def repositories_for(plan: dict, language: str, capability: str) -> list[dict]:
    names = {x["repository"] for x in plan["tasks"] if x["language"] == language and x["capability"] == capability and x["status"] == "scheduled"}
    return [x for x in plan["repositories"] if x["name"] in names and x["enabled"] and x["status"] == "available"]


def slug(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in value).strip("-")


def absolute_repo(aosp: Path, repo: dict) -> Path:
    path = Path(repo["path"])
    return path if path.is_absolute() else aosp / path


def scan_paths(aosp: Path, repo: dict) -> list[Path]:
    root = absolute_repo(aosp, repo)
    return [root / x for x in repo.get("include", []) if (root / x).exists()] or [root]


def repository_for_source(aosp: Path, repositories: list[dict], source: Path | str) -> dict | None:
    path = Path(source).resolve()
    candidates = sorted(repositories, key=lambda x: len(str(absolute_repo(aosp, x))), reverse=True)
    for repo in candidates:
        root = absolute_repo(aosp, repo).resolve()
        try:
            path.relative_to(root)
            return repo
        except ValueError:
            continue
    return None


def source_allowed(aosp: Path, repo: dict, source: Path | str, defaults: list[str]) -> bool:
    path = Path(source).resolve(); root = absolute_repo(aosp, repo).resolve()
    try: relative = path.relative_to(root)
    except ValueError: return False
    patterns = list(defaults) + list(repo.get("exclude", []))
    return not any(pattern in relative.parts or relative.match(pattern) or relative.match(f"**/{pattern}") for pattern in patterns)


def node_sources(db: Path) -> dict[str, str | None]:
    with sqlite3.connect(db) as c:
        return dict(c.execute("SELECT node_id, source_path FROM node"))


def run_java(plan: dict, db: Path, raw_dir: Path) -> list[dict]:
    aosp = Path(plan["aosp_root"]); raw_dir.mkdir(parents=True, exist_ok=True)
    duplicates: list[dict] = []
    for repo in repositories_for(plan, "java", "symbols"):
        output = raw_dir / f"{slug(repo['name'])}.jsonl"
        command = ["ctags", "--languages=Java", "--output-format=json", "--fields=+nKSEi", "-R", "-f", str(output)]
        for pattern in list(plan.get("default_exclude", [])) + list(repo.get("exclude", [])):
            command.append(f"--exclude={pattern}")
        command.extend(str(x) for x in scan_paths(aosp, repo))
        subprocess.run(command, check=True)
        before = node_sources(db)
        subprocess.run([sys.executable, "-m", "collectors.source.ctags_importer", str(output), str(db), str(aosp)], check=True)
        after = node_sources(db)
        for node_id, old_path in before.items():
            new_path = after.get(node_id)
            if old_path and new_path and old_path != new_path:
                duplicates.append({"node_id": node_id, "first_source": old_path, "replacement_source": new_path, "repository": repo["name"]})
    (raw_dir / "duplicate-qualified-names.json").write_text(json.dumps(duplicates, indent=2), encoding="utf-8")
    return duplicates


def run_kotlin(plan: dict, db: Path, raw_dir: Path) -> list[dict]:
    aosp = Path(plan["aosp_root"]); raw_dir.mkdir(parents=True, exist_ok=True)
    duplicates: list[dict] = []
    for repo in repositories_for(plan, "kotlin", "symbols"):
        output = raw_dir / f"{slug(repo['name'])}-kotlin.jsonl"
        command = ["ctags", "--languages=Kotlin", "--output-format=json", "--fields=+nKSEi", "-R", "-f", str(output)]
        for pattern in list(plan.get("default_exclude", [])) + list(repo.get("exclude", [])):
            command.append(f"--exclude={pattern}")
        command.extend(str(x) for x in scan_paths(aosp, repo))
        subprocess.run(command, check=True)
        before = node_sources(db)
        subprocess.run([sys.executable, "-m", "collectors.source.ctags_importer", str(output), str(db), str(aosp), "--language", "kotlin"], check=True)
        after = node_sources(db)
        for node_id, old_path in before.items():
            new_path = after.get(node_id)
            if old_path and new_path and old_path != new_path:
                duplicates.append({"node_id": node_id, "first_source": old_path, "replacement_source": new_path, "repository": repo["name"]})
    (raw_dir / "duplicate-qualified-names-kotlin.json").write_text(json.dumps(duplicates, indent=2), encoding="utf-8")
    return duplicates


def run_inheritance(plan_path: Path, plan: dict, db: Path, raw_dir: Path, report_dir: Path) -> None:
    aosp = Path(plan["aosp_root"]); report_dir.mkdir(parents=True, exist_ok=True)
    for lang in ["java", "kotlin"]:
        for repo in repositories_for(plan, lang, "inheritance"):
            suffix = "-kotlin" if lang == "kotlin" else ""
            source = raw_dir / f"{slug(repo['name'])}{suffix}.jsonl"
            if source.is_file():
                subprocess.run([sys.executable, "-m", "collectors.source.java_inheritance_importer",
                    "--ctags-jsonl", str(source), "--source-root", str(aosp), "--db", str(db),
                    "--report", str(report_dir / f"{slug(repo['name'])}{suffix}.json")], check=True)


def annotate(db: Path, plan: dict) -> None:
    repos = sorted((x for x in plan["repositories"] if x["enabled"]), key=lambda x: len(x["path"]), reverse=True)
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT node_id, source_path, properties_json FROM node WHERE source_path IS NOT NULL").fetchall()
        for node_id, source, raw in rows:
            repo = next((x for x in repos if source == x["path"] or source.startswith(x["path"].rstrip("/") + "/")), None)
            if not repo: continue
            props = json.loads(raw or "{}")
            props.update({"repository": repo["name"], "repository_path": repo["path"],
                          "repository_relative_path": source[len(repo["path"]):].lstrip("/")})
            c.execute("UPDATE node SET properties_json=? WHERE node_id=?", (json.dumps(props, sort_keys=True), node_id))


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("command", choices=["java", "kotlin", "inheritance", "annotate"])
    p.add_argument("--plan", type=Path, required=True); p.add_argument("--db", type=Path, required=True)
    p.add_argument("--ctags-dir", type=Path, default=Path("data/raw/ctags")); p.add_argument("--report-dir", type=Path, default=Path("data/raw/inheritance"))
    a = p.parse_args(); plan = load_plan(a.plan)
    if a.command == "java": run_java(plan, a.db, a.ctags_dir)
    elif a.command == "kotlin": run_kotlin(plan, a.db, a.ctags_dir)
    elif a.command == "inheritance": run_inheritance(a.plan, plan, a.db, a.ctags_dir, a.report_dir)
    else: annotate(a.db, plan)
    return 0


if __name__ == "__main__": raise SystemExit(main())
