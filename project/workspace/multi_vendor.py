from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from graph.writer import Edge, GraphWriter, Node


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VendorImportError(RuntimeError):
    pass


def ensure_staged_database(database: Path, staging_root: Path) -> None:
    expected = (staging_root / "android_context.db").resolve()
    if database.resolve() != expected:
        raise VendorImportError(
            f"Vendor import requires the active staged database: {expected}"
        )


def _manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _definition_ids(database: Path, repository: str) -> list[str]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT node_id, properties_json
            FROM node
            WHERE node_type='SYMBOL_DEFINITION'
            """
        ).fetchall()
    return [
        node_id
        for node_id, raw in rows
        if json.loads(raw or "{}").get("repository") == repository
    ]


def _run_ctags(
    source_dir: Path,
    output: Path,
    language: str,
) -> bool:
    suffix = ".java" if language == "java" else ".kt"
    if not any(source_dir.rglob(f"*{suffix}")):
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ctags",
            f"--languages={language.capitalize()}",
            "--output-format=json",
            "--fields=+nKSEie",
            "-R",
            "-f",
            str(output),
            str(source_dir),
        ],
        check=True,
    )
    return True


def import_vendor_artifacts(
    manifest_path: Path,
    database: Path,
    staging_root: Path,
    ctags_dir: Path,
) -> dict[str, object]:
    ensure_staged_database(database, staging_root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha256 = _manifest_sha256(manifest_path)
    imported: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for artifact in manifest.get("artifacts", []):
        if artifact.get("status") not in {"prepared", "degraded"}:
            skipped.append(
                {
                    "artifact_sha256": artifact.get("artifact_sha256"),
                    "reason": f"status:{artifact.get('status')}",
                }
            )
            continue
        source_dir = Path(str(artifact["source_dir"]))
        if not source_dir.is_dir():
            raise VendorImportError(
                f"Prepared Vendor source directory is missing: {source_dir}"
            )
        artifact_sha256 = str(artifact["artifact_sha256"])
        repository = f"vendor-artifact:{artifact_sha256}"
        artifact_node_id = f"VENDOR_ARTIFACT:{artifact_sha256}"
        writer = GraphWriter(database, source_revision=artifact_sha256)
        try:
            writer.upsert_node(
                Node(
                    node_id=artifact_node_id,
                    node_type="VENDOR_ARTIFACT",
                    qualified_name=artifact_sha256,
                    display_name=str(artifact["artifact_name"]),
                    properties={
                        **artifact,
                        "manifest_sha256": manifest_sha256,
                    },
                    source_path=str(artifact["artifact_path"]),
                    extractor="vendor-artifacts-v0.1",
                )
            )
        finally:
            writer.close()

        artifact_ctags = ctags_dir / artifact["cache_key"]
        language_outputs: list[tuple[str, Path]] = []
        for language in ("java", "kotlin"):
            output = artifact_ctags / f"{language}.jsonl"
            if _run_ctags(source_dir, output, language):
                language_outputs.append((language, output))
                command = [
                    sys.executable,
                    "-m",
                    "collectors.source.ctags_importer",
                    str(output),
                    str(database),
                    str(source_dir),
                    "--repository",
                    repository,
                    "--source-revision",
                    artifact_sha256,
                ]
                if language == "kotlin":
                    command.extend(["--language", "kotlin"])
                subprocess.run(command, check=True, cwd=PROJECT_ROOT)
                inheritance_report = artifact_ctags / f"{language}-inheritance.json"
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "collectors.source.java_inheritance_importer",
                        "--ctags-jsonl",
                        str(output),
                        "--source-root",
                        str(source_dir),
                        "--db",
                        str(database),
                        "--report",
                        str(inheritance_report),
                        "--source-revision",
                        artifact_sha256,
                        "--artifact-sha",
                        artifact_sha256,
                    ],
                    check=True,
                    cwd=PROJECT_ROOT,
                )

        definitions = _definition_ids(database, repository)
        writer = GraphWriter(database, source_revision=artifact_sha256)
        try:
            for definition_id in definitions:
                writer.upsert_edge(
                    Edge(
                        edge_type="DERIVED_FROM_ARTIFACT",
                        from_node_id=definition_id,
                        to_node_id=artifact_node_id,
                        properties={
                            "artifact_sha256": artifact_sha256,
                            "manifest_sha256": manifest_sha256,
                            "decompilation_status": artifact["status"],
                        },
                        extractor="vendor-artifacts-v0.1",
                    )
                )
        finally:
            writer.close()
        imported.append(
            {
                "artifact_sha256": artifact_sha256,
                "repository": repository,
                "definitions": len(definitions),
                "languages": [language for language, _ in language_outputs],
                "status": artifact["status"],
            }
        )
    return {
        "schema_version": "1.0",
        "manifest_sha256": manifest_sha256,
        "artifacts": imported,
        "skipped": skipped,
        "summary": {
            "artifacts_imported": len(imported),
            "artifacts_skipped": len(skipped),
            "definitions_traced": sum(
                int(item["definitions"]) for item in imported
            ),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import staged Vendor artifacts")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--ctags-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    report = import_vendor_artifacts(
        parsed.manifest,
        parsed.db,
        parsed.staging_root,
        parsed.ctags_dir,
    )
    parsed.report.parent.mkdir(parents=True, exist_ok=True)
    parsed.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
