from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import collectors.source.ctags_importer as importer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            (PROJECT_ROOT / "storage/schema.sql").read_text(encoding="utf-8")
        )


def test_parse_line_range_uses_ctags_end() -> None:
    assert importer.parse_line_range({"line": 10, "end": 27}) == (10, 27)


def test_parse_line_range_never_ends_before_start() -> None:
    assert importer.parse_line_range({"line": 10}) == (10, 10)
    assert importer.parse_line_range({"line": 10, "end": 4}) == (10, 10)
    assert importer.parse_line_range({}) == (None, None)


def test_resolve_line_range_balances_kotlin_method_body(tmp_path: Path) -> None:
    source = tmp_path / "KBase.kt"
    source.write_text(
        "package common\n"
        "class KBase {\n"
        "    fun boundedKotlin() {\n"
        "        val text = \"} is not the end\"\n"
        "        // } is not the end either\n"
        "        if (text.isNotEmpty()) {\n"
        "            println(text)\n"
        "        }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    assert importer.resolve_line_range(
        {
            "line": 3,
            "kind": "method",
            "path": str(source),
        }
    ) == (3, 9)


def test_resolve_line_range_keeps_unproven_expression_body_single_line(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Expression.kt"
    source.write_text(
        "package common\nfun expressionBody() = 42\n",
        encoding="utf-8",
    )

    assert importer.resolve_line_range(
        {
            "line": 2,
            "kind": "method",
            "path": str(source),
        }
    ) == (2, 2)


def test_first_pass_persists_ctags_end(tmp_path: Path) -> None:
    source_root = tmp_path / "aosp"
    source = source_root / "frameworks/base/A.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package example;\n"
        "class A {\n"
        "    void bounded() {\n"
        "        int value = 1;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    ctags = tmp_path / "tags.jsonl"
    ctags.write_text(
        json.dumps(
            {
                "_type": "tag",
                "name": "bounded",
                "path": str(source),
                "line": 3,
                "end": 5,
                "kind": "method",
                "signature": "()",
                "scope": "A",
                "scopeKind": "class",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "graph.db"
    create_database(database)

    imported, skipped = importer.first_pass(ctags, database, source_root)

    assert (imported, skipped) == (1, 0)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT line_start, line_end FROM node WHERE node_type='JAVA_METHOD'"
        ).fetchone() == (3, 5)
