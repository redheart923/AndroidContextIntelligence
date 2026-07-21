from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROJECT_README = ROOT / "project/README.md"
DOC_INDEX = ROOT / "doc/README.md"


def test_root_readme_documents_canonical_distribution_and_commands() -> None:
    text = README.read_text(encoding="utf-8")

    assert "`project/` 是唯一" in text
    assert "不能只复制" in text
    assert "./setup.sh --fresh" in text
    assert "./setup.sh --upgrade" in text
    assert "./setup.sh --verify-only" in text
    assert "./setup.sh --fresh --rebuild" in text
    assert "scripts/verify_project_install.py" in text


def test_readmes_do_not_claim_unverified_permission_or_vendor_completion() -> None:
    combined = README.read_text(encoding="utf-8") + PROJECT_README.read_text(
        encoding="utf-8"
    )

    assert "2M+" not in combined
    assert "Permission Graph 已经" not in combined
    assert "Phase 2a)**" not in combined
    assert "按顺序调用 `installers/` 目录下的 6 个脚本" not in combined


def test_documentation_index_links_repository_review() -> None:
    text = DOC_INDEX.read_text(encoding="utf-8")

    assert "Repository Architecture Review" in text
    assert "reviews/2026-07-21-repository-architecture-review.md" in text


def test_primary_documentation_local_links_resolve() -> None:
    failures: list[str] = []
    for document in (README, PROJECT_README, DOC_INDEX):
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            if not (document.parent / path_text).resolve().exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")

    assert failures == []
