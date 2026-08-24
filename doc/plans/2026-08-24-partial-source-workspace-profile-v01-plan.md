# Partial Source Workspace Profile v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Atomically publish a provenance-bound graph from one or more partial source repositories without weakening AOSP publication gates or implying full-AOSP coverage.

**Architecture:** Add explicit scope identity to configuration and plans, then place a two-phase source-scope validator around the existing staged build. Bind the verified scope report to provenance, `GRAPH_BUILD`, manifests, retained history, fingerprints, and query output.

**Tech Stack:** Python 3.11+, dataclasses, `tomllib`, SQLite, Bash, pytest, canonical JSON and SHA-256.

**Spec:** `doc/designs/2026-08-24-partial-source-workspace-profile-v01-design.md`

## Global Constraints

- `analysis_scope` accepts exactly `aosp` or `partial`; omission means `aosp`.
- Scope is independent of `strict` and `strict_capability`.
- Partial mode retains FK, collision, permission, provenance, correction, and requested strict-capability gates.
- Partial mode does not require LocalServices, AMS, PMS, SystemUI, or another Framework-specific representative fact.
- AOSP mode retains existing representative gates.
- `full_aosp_coverage` remains `false` in v0.1 and never controls execution.
- Discover-only and plan-only remain diagnostic and skip publication preflight.
- Missing CodeQL keeps call/dataflow degraded in non-strict mode; strict capability requests fail.
- Failed full rebuilds preserve the live database byte-for-byte.
- Machine-local paths remain outside canonical Git configuration.
- Work is committed in reviewable units on `codex/partial-source-workspace-v01`, reviewed, and merged to `main`.

---

### Task 1: Scope Identity in Configuration and Plans

**Files:**
- Modify: `project/workspace/models.py`
- Modify: `project/workspace/config.py`
- Modify: `project/workspace/planner.py`
- Modify: `project/config/source_roots.default.toml`
- Modify: `project/config/source_roots.local.toml.example`
- Modify: `project/tests/unit/test_workspace_v01.py`
- Modify: `project/tests/unit/test_permission_workspace.py`

**Interfaces:**
- Produces `WorkspaceConfig.analysis_scope` and `WorkspacePlan.analysis_scope` as `Literal["aosp", "partial"]`.
- Produces `WorkspacePlan.full_aosp_coverage: bool`.
- Serializes both fields in `WorkspacePlan.to_dict()`.

- [ ] **Step 1: Write failing tests**

Add tests for default `aosp`, `partial` round-trip, and rejection of `""`, `"AOSP"`, `"full"`, and `"vendor"`.

```python
def test_partial_scope_round_trips_through_plan(tmp_path: Path) -> None:
    config, registry = workspace_fixture(tmp_path, analysis_scope="partial")
    payload = build_workspace_plan(config, registry).to_dict()
    assert payload["analysis_scope"] == "partial"
    assert payload["full_aosp_coverage"] is False
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest -q project/tests/unit/test_workspace_v01.py project/tests/unit/test_permission_workspace.py`.

Expected: failures for absent scope fields.

- [ ] **Step 3: Implement exact parsing and serialization**

```python
AnalysisScope = Literal["aosp", "partial"]
analysis_scope = workspace.get("analysis_scope", "aosp")
if analysis_scope not in {"aosp", "partial"}:
    raise ValueError(
        "workspace.analysis_scope must be exactly 'aosp' or 'partial'"
    )
```

Pass the parsed value into `WorkspacePlan`; set `full_aosp_coverage=False` for both profiles.

- [ ] **Step 4: Update configuration examples**

Set `analysis_scope = "aosp"` in the default file and add a commented `analysis_scope = "partial"` example to the local template.

- [ ] **Step 5: Verify and commit**

Run `python -m pytest -q project/tests/unit/test_workspace_v01.py project/tests/unit/test_permission_workspace.py project/tests/integration/test_multi_repository_pipeline.py`.

Commit: `git commit -m "feat: add workspace analysis scope identity"`.

---

### Task 2: Deterministic Two-Phase Scope Validator

**Files:**
- Create: `project/workspace/source_scope_validation.py`
- Create: `project/tests/unit/test_source_scope_validation.py`

**Interfaces:**
- `SourceScopeError(RuntimeError)` exposes stable reason codes in messages.
- `validate_preflight(plan: dict[str, object]) -> dict[str, object]`.
- `validate_post_import(plan, database, capability_report, provenance, build_id) -> dict[str, object]`.
- `scope_report_fingerprint(payload: object) -> str`.
- `write_scope_report(path: Path, payload: dict[str, object]) -> None` performs atomic replacement.

- [ ] **Step 1: Write failing preflight tests**

Parameterize no enabled repository, missing repository, zero inventory, no recognized language, no scheduled parser, duplicate identity, and missing revision/inventory digest. Assert reason codes:

```text
no_enabled_repository
missing_enabled_repository
empty_repository_inventory
no_recognized_language
no_scheduled_parser
scope_repository_mismatch
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest -q project/tests/unit/test_source_scope_validation.py`.

Expected: import failure because the module is absent.

- [ ] **Step 3: Implement canonical fingerprinting**

```python
def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

def scope_report_fingerprint(payload: object) -> str:
    value = payload
    if isinstance(payload, dict) and "fingerprint" in payload:
        value = {key: item for key, item in payload.items() if key != "fingerprint"}
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
```

Sort repositories by `(name, path)`, capabilities by key, and errors by `(reason_code, repository, capability)`.

- [ ] **Step 4: Implement preflight**

Validate all explicit conditions before returning a `preflight_passed` report. Include schema version, scope, full coverage flag, sorted enabled repository identities, scheduled count, zero source-node count, empty capability counts, and errors.

- [ ] **Step 5: Write failing post-import tests**

Cover empty graph, wrong repository ownership, unexecuted scheduled capability, strict degraded capability, provenance mismatch, valid partial graph without LocalServices, and AOSP graph without LocalServices.

```python
assert validate_post_import(
    partial_plan(), graph_database(tmp_path), supported_report(),
    matching_provenance(), "fixture-build"
)["status"] == "passed"
```

- [ ] **Step 6: Implement post-import rules**

Count non-`GRAPH_BUILD` nodes with a source path, require at least one to belong to an enabled repository, match scheduled tasks to `supported` or `degraded`, enforce requested strict capability as `supported`, and compare exact `(name, path, revision, inventory_sha256)` repository tuples with provenance.

Only `aosp` runs the `EXPOSED_AS_LOCAL_SERVICE` count and raises `aosp_representative_evidence_missing` on zero.

- [ ] **Step 7: Implement atomic report writing**

Write a same-directory temporary file, flush, `os.fsync`, and `os.replace`; include and verify the self-fingerprint.

- [ ] **Step 8: Verify and commit**

Run `python -m pytest -q project/tests/unit/test_source_scope_validation.py project/tests/unit`.

Commit: `git commit -m "feat: validate partial source workspace scope"`.

---

### Task 3: Profile-Aware Rebuild and Atomic Failure Gates

**Files:**
- Modify: `project/workspace/source_scope_validation.py`
- Modify: `project/scripts/rebuild_all.sh`
- Modify: `project/tests/integration/test_atomic_rebuild.py`
- Create: `project/tests/integration/test_partial_source_atomic_rebuild.py`

**Interfaces:**
- CLI subcommands `preflight` and `post-import`.
- Staged artifact `workspace/source-scope-validation.json`.
- Preflight runs after plan creation and before schema/collectors.
- Post-import runs after runtime coverage/provenance and before prepare/publish.

- [ ] **Step 1: Write failing shell-order tests**

```python
assert script.index("source_scope_validation preflight") < script.index(
    'sqlite3 "$STAGED_DB" <'
)
assert script.index("workspace.coverage_validation") < script.index(
    "source_scope_validation post-import"
)
assert script.index("source_scope_validation post-import") < script.index(
    "workspace.build_publish prepare"
)
assert "LOCAL_SERVICE_COUNT=" not in script
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest -q project/tests/integration/test_atomic_rebuild.py project/tests/integration/test_partial_source_atomic_rebuild.py`.

- [ ] **Step 3: Add validator CLI and pipeline calls**

Implement argparse subcommands. Set `SCOPE_REPORT="$STAGED_WORKSPACE/source-scope-validation.json"`; run preflight before SQLite schema creation and post-import after provenance validation.

Remove the unconditional shell LocalServices gate. Read verified `analysis_scope` and print AMS/PMS query output only for `aosp`; the validator retains the AOSP gate.

- [ ] **Step 4: Add partial atomic fixture tests**

Prove a Java-only repository without LocalServices publishes. Prove missing, empty, zero-node, and strict-call-graph fixtures fail while leaving sentinel live DB bytes unchanged.

- [ ] **Step 5: Verify shell and integration behavior**

Run:

```powershell
wsl.exe bash -lc "bash -n /mnt/d/AndroidContextIntelligence/.worktrees/partial-source-workspace-v01/project/scripts/rebuild_all.sh"
python -m pytest -q project/tests/integration/test_atomic_rebuild.py project/tests/integration/test_partial_source_atomic_rebuild.py project/tests/integration/test_codeql_atomic_rebuild.py
```

- [ ] **Step 6: Commit**

Commit: `git commit -m "feat: enforce scope-aware atomic rebuild gates"`.

---

### Task 4: Provenance and Publication Binding

**Files:**
- Modify: `project/workspace/provenance.py`
- Modify: `project/workspace/build_publish.py`
- Modify: `project/scripts/rebuild_all.sh`
- Modify: `project/tests/unit/test_provenance.py`
- Modify: `project/tests/unit/test_build_publish.py`
- Modify: `project/tests/unit/test_graph_fingerprint.py`

**Interfaces:**
- `collect_provenance(..., scope_report: Path | None = None)`.
- `record_graph_build(..., scope_report: Path | None = None)`.
- Prepare CLI accepts `--scope-report`.
- Provenance, `GRAPH_BUILD`, and build manifest share `source_scope`, `source_scope_sha256`, and `analysis_scope`.

- [ ] **Step 1: Write failing provenance tests**

Assert the report payload/digest are present and tampering is rejected by strict provenance validation.

- [ ] **Step 2: Write failing publication tests**

Reject build-ID mismatch, repository mismatch, non-passed report, digest mismatch, and scope disagreement. Assert valid scope data is identical in `GRAPH_BUILD.properties_json` and `build-manifest.json`.

- [ ] **Step 3: Verify RED**

Run `python -m pytest -q project/tests/unit/test_provenance.py project/tests/unit/test_build_publish.py project/tests/unit/test_graph_fingerprint.py`.

- [ ] **Step 4: Bind report into provenance and publication**

Store:

```python
"source_scope": {
    "path": str(scope_report.resolve()),
    "sha256": hashlib.sha256(scope_report.read_bytes()).hexdigest(),
    "payload": scope_payload,
}
```

Pass `--scope-report` from the shell to provenance collection and build preparation. Validate scope agreement before recording the graph build.

- [ ] **Step 5: Make publication fingerprint scope-aware**

Keep fact fingerprints stable; add `publication_scope = scope_report_fingerprint(scope_payload)` to `semantic-fingerprints.json`. Prove identical facts under different scopes retain the fact fingerprint but change the publication fingerprint.

- [ ] **Step 6: Verify retained history**

Assert history contains `workspace/source-scope-validation.json` and its manifest contains `analysis_scope` and `source_scope_sha256`.

- [ ] **Step 7: Verify and commit**

Run `python -m pytest -q project/tests/unit/test_provenance.py project/tests/unit/test_build_publish.py project/tests/unit/test_graph_fingerprint.py project/tests/integration/test_atomic_rebuild.py`.

Commit: `git commit -m "feat: bind source scope to graph publication"`.

---

### Task 5: Scope Query and User Documentation

**Files:**
- Create: `project/queries/source_scope_summary.sql`
- Modify: `project/queries/workspace_coverage_summary.sql`
- Modify: `project/README.md`
- Modify: `project/INSTALLATION_MANIFEST.txt`
- Modify: `doc/README.md`
- Modify: `tests/test_documentation_contract.py`
- Modify: `tests/test_canonical_project.py`

**Interfaces:**
- Query returns `analysis_scope`, `full_aosp_coverage`, `warning`, and `scope_report_sha256`.
- Partial warning is exactly `PARTIAL SOURCE GRAPH - NOT FULL AOSP`.

- [ ] **Step 1: Write failing payload/documentation tests**

Require the query, partial configuration, warning, strict capability command, and scope report to appear in canonical documentation.

- [ ] **Step 2: Verify RED**

Run `python -m pytest -q tests/test_documentation_contract.py tests/test_canonical_project.py`.

- [ ] **Step 3: Add scope query**

```sql
SELECT
  json_extract(properties_json, '$.analysis_scope') AS analysis_scope,
  CAST(json_extract(properties_json, '$.full_aosp_coverage') AS INTEGER)
    AS full_aosp_coverage,
  CASE json_extract(properties_json, '$.analysis_scope')
    WHEN 'partial' THEN 'PARTIAL SOURCE GRAPH - NOT FULL AOSP'
    ELSE '' END AS warning,
  json_extract(properties_json, '$.source_scope_sha256')
    AS scope_report_sha256
FROM node
WHERE node_type='GRAPH_BUILD'
ORDER BY qualified_name DESC LIMIT 1;
```

- [ ] **Step 4: Document exact usage**

Document AOSP-relative repositories, arbitrary `extra_repositories`, `--plan-only`, normal rebuild, strict capability, query invocation, CodeQL degradation, and the non-full-AOSP boundary.

- [ ] **Step 5: Verify and commit**

Run `python -m pytest -q tests/test_documentation_contract.py tests/test_canonical_project.py tests/test_payload_manifest.py tests/test_project_payload.py`.

Commit: `git commit -m "docs: expose partial source graph scope"`.

---

### Task 6: Determinism and Upgrade Acceptance

**Files:**
- Modify: `project/tests/integration/test_partial_source_atomic_rebuild.py`
- Modify: `project/tests/integration/test_multi_repository_pipeline.py`
- Modify: `tests/test_install_project.py`
- Modify: `tests/test_installer_payload_sync.py`
- Modify: `tests/test_setup_contract.py`
- Modify: `scripts/project_payload.py` only if recursive payload discovery does not already include new files.

**Interfaces:**
- Proves two-repository partial publication and cross-repository facts.
- Proves identical builds have identical scope/publication fingerprints.
- Proves upgrade preserves machine-local partial configuration byte-for-byte.

- [ ] **Step 1: Add two-repository partial fixture**

Repository A defines a base type/AIDL contract; repository B extends or implements it. Neither contains LocalServices. Assert repository-scoped definitions and cross-repository inheritance after publication.

- [ ] **Step 2: Add deterministic build test**

Run independent staged builds with different build IDs and assert equal scope fingerprint, publication-scope fingerprint, and whole-graph fact fingerprint after excluding documented run identity.

- [ ] **Step 3: Add source mutation rejection**

Mutate a fixture after planning and before provenance/post-import validation; require source-change/scope-mismatch failure and unchanged live DB bytes.

- [ ] **Step 4: Add upgrade preservation test**

Install, write a local `analysis_scope = "partial"` plus `extra_repositories`, upgrade, and assert the file bytes are unchanged.

- [ ] **Step 5: Run all suites and commit**

Run `python -m pytest -q project/tests` and `python -m pytest -q tests`.

Commit: `git commit -m "test: accept deterministic partial source builds"`.

---

### Task 7: Final Verification, Review, Merge, and Cleanup

**Files:**
- Create: `doc/reviews/2026-08-24-partial-source-workspace-v01-acceptance.md`
- Modify: `doc/README.md`

**Interfaces:**
- Acceptance record separates fixture evidence, WSL smoke evidence, and full-AOSP `NOT RUN` boundaries.
- Reviewed feature branch is merged to `main` and physical worktree removed.

- [ ] **Step 1: Run static gates**

```powershell
python -m compileall -q project/workspace project/collectors scripts tests
wsl.exe bash -lc "bash -n /mnt/d/AndroidContextIntelligence/.worktrees/partial-source-workspace-v01/project/scripts/rebuild_all.sh"
git diff --check main...HEAD
```

- [ ] **Step 2: Run full automated tests**

Run `python -m pytest -q project/tests` and `python -m pytest -q tests`.

- [ ] **Step 3: Run WSL partial smoke when sources are available**

Deploy the feature payload, preserve local configuration, run plan-only and rebuild, then query `queries/source_scope_summary.sql`. Record exact repository revisions and unsupported/degraded capabilities. If unavailable, record `NOT RUN` with the concrete blocker; do not claim success.

- [ ] **Step 4: Write and commit acceptance evidence**

Record commands, exit codes, test counts, scope digest, repository revisions, query warning, remaining limitations, and:

```text
Fixture partial publication: PASS
AOSP-profile regression: PASS
Atomic failure preservation: PASS
WSL local partial smoke: PASS or NOT RUN with reason
Full AOSP build/graph acceptance: NOT RUN
```

Commit: `git commit -m "docs: record partial source profile acceptance"`.

- [ ] **Step 5: Request code review and fix confirmed findings**

Use `superpowers:requesting-code-review`; review compatibility, gate ordering, profile separation, provenance agreement, atomicity, hashing, shell quoting, and installer payload coverage. Add tests for every confirmed defect.

- [ ] **Step 6: Re-run completion gates**

Run both full suites, `git diff --check main...HEAD`, and `git status --short`. Require green tests and a clean worktree.

- [ ] **Step 7: Merge and clean worktree**

Use `superpowers:finishing-a-development-branch`, merge `codex/partial-source-workspace-v01` to `main`, verify commits on main, and remove the physical worktree. Retain the branch/history unless explicitly asked to delete it.

## Self-Review Record

- Spec coverage: Tasks 1-7 cover scope identity, pre/post validation, partial/AOSP gates, CodeQL degradation and strict behavior, provenance, publication, queries, deterministic hashing, atomic failure, upgrade preservation, WSL evidence, review, merge, and cleanup.
- Placeholder scan: no deferred implementation placeholders or undefined generic validation steps remain.
- Type consistency: `analysis_scope`, `full_aosp_coverage`, `source_scope`, `source_scope_sha256`, `scope_report_fingerprint`, `validate_preflight`, and `validate_post_import` are consistent across tasks.
- Scope boundary: generic CodeQL DB construction, native parsing, Soong graphs, Web serving, and full-AOSP acceptance remain outside v0.1.
