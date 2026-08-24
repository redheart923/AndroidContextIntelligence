from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROJECT_README = ROOT / "project/README.md"
DOC_INDEX = ROOT / "doc/README.md"
OLD_PERMISSION_PLAN = (
    ROOT / "doc/plans/2026-07-17-permission-enforcement-graph-v01-plan.md"
)


PERMISSION_EDGE_TYPES = {
    "DECLARES_PERMISSION",
    "REQUESTS_PERMISSION",
    "ALLOWLISTS_PRIVILEGED_PERMISSION",
    "DENIES_PRIVILEGED_PERMISSION",
    "DEFAULT_GRANTS_PERMISSION",
    "REQUIRES_PERMISSION",
    "CHECKS_PERMISSION",
    "ENFORCES_PERMISSION",
}


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


def test_readmes_document_permission_semantics_contract() -> None:
    for document in (README, PROJECT_README):
        text = document.read_text(encoding="utf-8")
        assert "permission_semantics" in text
        assert "permission-semantics-report.json" in text
        assert "Allowlist is policy eligibility, not a runtime grant." in text
        assert "Check observes or returns; enforce denies by raising an error." in text
        assert all(edge_type in text for edge_type in PERMISSION_EDGE_TYPES)


def test_old_permission_plan_is_explicitly_superseded() -> None:
    text = OLD_PERMISSION_PLAN.read_text(encoding="utf-8")

    assert "Status: Superseded" in text
    assert "../designs/2026-07-21-permission-semantics-graph-v01-design.md" in text
    assert "2026-07-22-permission-semantics-graph-v01-plan.md" in text


def test_documentation_index_links_permission_acceptance() -> None:
    text = DOC_INDEX.read_text(encoding="utf-8")

    assert "Permission Semantics Graph v0.1 Acceptance" in text
    assert "reviews/2026-07-22-permission-semantics-graph-v01-acceptance.md" in text


def test_readmes_document_call_dataflow_workflow() -> None:
    combined = README.read_text(encoding="utf-8") + PROJECT_README.read_text(
        encoding="utf-8"
    )
    for token in (
        "prepare_codeql.sh",
        "--cache-root",
        "--codeql-db",
        "call_graph",
        "interprocedural_dataflow",
        "MUST_CALL",
        "MAY_CALL",
        "FACT_CORRECTION",
        "graph_diff.py",
    ):
        assert token in combined


def test_documentation_index_links_call_dataflow_acceptance() -> None:
    text = DOC_INDEX.read_text(encoding="utf-8")

    assert "Java/Kotlin Call and Dataflow Graph v0.1 Acceptance" in text
    assert "reviews/2026-08-20-java-kotlin-call-dataflow-v01-acceptance.md" in text


def test_project_readme_documents_partial_source_profile() -> None:
    text = PROJECT_README.read_text(encoding="utf-8")

    for token in (
        'analysis_scope = "partial"',
        "extra_repositories",
        "source-scope-validation.json",
        "queries/source_scope_summary.sql",
        "PARTIAL SOURCE GRAPH - NOT FULL AOSP",
        "--strict-capability",
        "call_graph",
        "degraded",
    ):
        assert token in text
