# Java/Kotlin Call and Interprocedural Dataflow Graph v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CodeQL-backed, repository-aware Java/Kotlin call graph and bounded System Service security dataflow graph with explicit uncertainty, reviewed corrections, reproducible evidence, and atomic publication.

**Architecture:** Preserve the existing Ctags/AIDL/Service/Permission graph as the stable base and add typed semantic tables plus graph projections. Prepare a real `java-kotlin` CodeQL database independently with an isolated AOSP output directory, validate its content-addressed manifest, run a version-locked query pack, reconcile CodeQL definitions to logical method nodes, materialize call/dataflow/guard/identity facts in staging, replay Git-managed corrections, and publish only after structural, provenance, fixture, strong-evidence, and determinism gates pass.

**Tech Stack:** Python 3.11+, SQLite, Bash, pytest, Universal Ctags, Git repo manifests, CodeQL CLI `java-kotlin`, QL modular dataflow API, AOSP Soong, TOML, JSONL.

**Spec:** `doc/designs/2026-08-20-java-kotlin-call-dataflow-graph-v01-design.md`

## Global Constraints

- Keep `node` and `edge` backward-compatible; dense semantic facts live in typed extension tables.
- CodeQL is a precision enhancement and evidence source, not a replacement for the deterministic base graph.
- Java/Kotlin extraction uses a real manual AOSP build; `build-mode=none` is forbidden because Kotlin would be excluded.
- Default real-AOSP acceptance targets are exactly `services` and `SystemUI`; product and variant are explicit inputs.
- Static call outcomes are `MUST_CALL`, `MAY_CALL`, and `UNRESOLVED_CALL`; `OBSERVED_CALL` is reserved for future runtime evidence.
- Data propagation, control guards, and Binder identity transitions remain separate facts and are composed only by `SECURITY_TRACE`.
- Only uniquely reconciled logical endpoints may back accepted call, flow, guard, or identity graph edges.
- Corrections are Git-managed TOML and support only `suppress`, `replace`, `annotate`, and `add`; raw extractor facts are retained.
- Stale, malformed, duplicate, or unapproved corrections block strict publication.
- Every mutation is implemented on `codex/java-kotlin-call-dataflow-v01` with one independently reviewable Git commit per task.
- Unit/fixture success is not full completion; final completion requires real AMS, PMS, Kotlin SystemUI, and two-build deterministic evidence in WSL.
- Never edit `/home/ts/android-context-intelligence/data/android_context.db` directly; rebuild through staging and atomic publication.

---

## File Structure

New focused modules and assets:

```text
project/
  codeql/
    qlpack.yml
    codeql-pack.lock.yml
    lib/SystemServiceModels.qll
    queries/CallSites.ql
    queries/SystemServiceDataflow.ql
    queries/SystemServiceGuards.ql
    queries/BinderIdentity.ql
    tests/call-sites/...
    tests/security-flow/...
  collectors/codeql/
    model.py                 # normalized immutable records
    decode.py                # BQRS/CSV -> JSONL records
    identity.py              # semantic-key reconciliation
    materializer.py          # typed tables and graph projections
    corrections.py           # validated effective-view overlay
  config/
    codeql.toml              # acceptance targets and scenario IDs
    corrections/.gitkeep
  storage/migrations/
    0001_call_dataflow.sql   # typed semantic schema
  workspace/
    schema_migrations.py     # staging-only migration runner
    codeql_database.py       # cache key and manifest validation
    codeql_runner.py         # query execution and result cache
    call_dataflow_validation.py
  scripts/
    prepare_codeql.py
    prepare_codeql.sh
    graph_diff.py
  queries/
    call_graph_summary.sql
    system_service_security_traces.sql
```

Existing files modified together with these units:

```text
project/config/parser_registry.toml
project/workspace/models.py
project/workspace/registry.py
project/workspace/planner.py
project/workspace/coverage_validation.py
project/workspace/provenance.py
project/workspace/build_publish.py
project/scripts/rebuild_all.sh
project/scripts/graph_fingerprint.py
project/storage/schema.sql
project/README.md
project/INSTALLATION_MANIFEST.txt
scripts/project_payload.py
tests/test_project_payload.py
README.md
doc/README.md
```

### Task 1: Capability-Specific Parser Routing

**Files:**
- Modify: `project/workspace/models.py`
- Modify: `project/workspace/registry.py`
- Modify: `project/workspace/planner.py`
- Modify: `project/workspace/coverage_validation.py`
- Modify: `project/config/parser_registry.toml`
- Test: `project/tests/unit/test_workspace_v01.py`
- Test: `project/tests/unit/test_coverage_validation.py`

**Interfaces:**
- Consumes: existing `ParserSpec`, `PlanTask`, `build_workspace_plan()`, and runtime evidence contracts.
- Produces: `ParserSpec.implementation_for(capability: str) -> str | None`; evidence contract `typed_table:<table_name>`; planned Java/Kotlin `call_graph` and `interprocedural_dataflow` tasks routed to `codeql_java_kotlin_importer`.

- [ ] **Step 1: Write the failing capability-routing tests**

```python
def test_capability_specific_implementation_overrides_default(tmp_path: Path) -> None:
    registry = tmp_path / "registry.toml"
    registry.write_text(
        """
[parsers.java]
implementation = "java_symbol_importer"
enabled = true
capabilities = ["symbols", "call_graph"]
[parsers.java.capability_implementations]
call_graph = "codeql_java_kotlin_importer"
[parsers.java.capability_quality]
symbols = "tags_only"
call_graph = "semantic"
[parsers.java.capability_evidence]
symbols = ["node_type_prefix:JAVA_"]
call_graph = ["typed_table:call_site"]
""",
        encoding="utf-8",
    )
    parser = load_parser_registry(registry)["java"]
    assert parser.implementation_for("symbols") == "java_symbol_importer"
    assert parser.implementation_for("call_graph") == "codeql_java_kotlin_importer"
```

```python
def test_typed_table_evidence_is_scoped_by_repository_and_language(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE call_site(call_site_id TEXT PRIMARY KEY, repository TEXT, source_path TEXT)"
        )
        connection.execute(
            "INSERT INTO call_site VALUES ('CALL_SITE:1','frameworks/base',"
            "'frameworks/base/packages/SystemUI/src/demo/Example.kt')"
        )
    plan = _plan()
    plan["tasks"] = [{
        "repository": "frameworks/base",
        "repository_path": "frameworks/base",
        "language": "kotlin",
        "capability": "call_graph",
        "parser": "codeql_java_kotlin_importer",
        "status": "scheduled",
        "files": 1,
        "quality": "semantic",
        "expected_evidence": ["typed_table:call_site"],
    }]
    assert evaluate_runtime_coverage(plan, database)[0]["status"] == "supported"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd project
python -m pytest -q \
  tests/unit/test_workspace_v01.py \
  tests/unit/test_coverage_validation.py
```

Expected: failure because `ParserSpec` has no `implementation_for`, capability overrides are rejected, and `typed_table` evidence is unsupported.

- [ ] **Step 3: Implement routing and a safe typed-table evidence allowlist**

Add to `ParserSpec`:

```python
capability_implementations: tuple[tuple[str, str], ...] = ()

def implementation_for(self, capability: str) -> str | None:
    if not self.enabled or capability not in self.capabilities:
        return None
    return dict(self.capability_implementations).get(
        capability,
        self.implementation or None,
    )
```

In `ParserRegistry.parser_for`, require `implementation_for(capability)` rather than the single default. In `build_workspace_plan`, assign `parser.implementation_for(capability)` to `PlanTask.parser`. Add only these tables to `coverage_validation.py`:

```python
TYPED_EVIDENCE_TABLES = {
    "semantic_definition",
    "call_site",
    "call_target",
    "dataflow_path",
    "security_trace",
}
```

For `typed_table:<name>`, issue a quoted constant query selected from that allowlist and filter its `repository` plus `source_path` suffix. Add `call_graph` and `interprocedural_dataflow` to both Java and Kotlin planner capabilities and registry declarations, with `semantic` quality and `typed_table:call_site` / `typed_table:dataflow_path` evidence.

- [ ] **Step 4: Run GREEN and full planner regression**

Run:

```bash
cd project
python -m pytest -q tests/unit/test_workspace_v01.py tests/unit/test_coverage_validation.py
```

Expected: all selected tests pass; existing parser implementations remain unchanged for symbols, inheritance, service registration, and permission semantics.

- [ ] **Step 5: Commit**

```bash
git add project/workspace/models.py project/workspace/registry.py \
  project/workspace/planner.py project/workspace/coverage_validation.py \
  project/config/parser_registry.toml \
  project/tests/unit/test_workspace_v01.py \
  project/tests/unit/test_coverage_validation.py
git commit -m "feat: route semantic capabilities to CodeQL"
```

### Task 2: Staging-Only Schema Migration and Typed Semantic Tables

**Files:**
- Create: `project/storage/migrations/0001_call_dataflow.sql`
- Create: `project/workspace/schema_migrations.py`
- Modify: `project/storage/schema.sql`
- Modify: `project/scripts/rebuild_all.sh`
- Test: `project/tests/unit/test_schema_migrations.py`
- Test: `project/tests/integration/test_atomic_rebuild.py`

**Interfaces:**
- Consumes: a staged SQLite database already initialized by `storage/schema.sql`.
- Produces: `apply_migrations(database: Path, migrations_dir: Path) -> tuple[str, ...]`; schema version `1`; all typed tables specified by the design.

- [ ] **Step 1: Write failing migration tests**

```python
def test_call_dataflow_migration_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    initialize_base_schema(database)
    first = apply_migrations(database, MIGRATIONS)
    second = apply_migrations(database, MIGRATIONS)
    assert first == ("0001_call_dataflow",)
    assert second == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"extraction_run", "semantic_definition", "call_site", "call_target",
            "program_value", "dataflow_path", "dataflow_step", "security_trace",
            "security_trace_step", "extraction_evidence", "fact_correction",
            "correction_application"} <= names
```

```python
def test_failed_migration_rolls_back_schema_version(tmp_path: Path) -> None:
    database = tmp_path / "graph.db"
    initialize_base_schema(database)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        "CREATE TABLE partial(value TEXT); INVALID SQL;", encoding="utf-8"
    )
    with pytest.raises(MigrationError):
        apply_migrations(database, migrations)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='partial'"
        ).fetchone()[0] == 0
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_schema_migrations.py`

Expected: import failure for `workspace.schema_migrations`.

- [ ] **Step 3: Implement transactional migrations and the exact schema**

`schema_migrations.py` must expose:

```python
class MigrationError(RuntimeError):
    pass

def apply_migrations(database: Path, migrations_dir: Path) -> tuple[str, ...]:
    ...
```

Use `BEGIN IMMEDIATE`, verify filenames match `NNNN_name.sql`, reject duplicate versions, execute each file in order, insert `(version, name, sha256, applied_at)` into `schema_migration`, set `PRAGMA user_version`, run `PRAGMA foreign_key_check`, and commit only if all steps succeed. `0001_call_dataflow.sql` must define all columns, checks, foreign keys, and indexes from design sections 6.2 and 6.3, including `program_value` and correction application state.

- [ ] **Step 4: Insert migration execution into staging rebuild**

Immediately after base schema creation in `rebuild_all.sh` add:

```bash
python -m workspace.schema_migrations \
  --db "$STAGED_DB" \
  --migrations "$PROJECT_ROOT/storage/migrations"
```

Add an integration assertion that migration execution appears after `storage/schema.sql` and before any importer.

- [ ] **Step 5: Run GREEN and foreign-key regression**

Run:

```bash
cd project
python -m pytest -q tests/unit/test_schema_migrations.py tests/integration/test_atomic_rebuild.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add project/storage/schema.sql project/storage/migrations/0001_call_dataflow.sql \
  project/workspace/schema_migrations.py project/scripts/rebuild_all.sh \
  project/tests/unit/test_schema_migrations.py \
  project/tests/integration/test_atomic_rebuild.py
git commit -m "feat: add typed call dataflow schema migrations"
```

### Task 3: Verified Content-Addressed CodeQL Database Preparation

**Files:**
- Create: `project/workspace/codeql_database.py`
- Create: `project/scripts/prepare_codeql.py`
- Create: `project/scripts/prepare_codeql.sh`
- Create: `project/config/codeql.toml`
- Test: `project/tests/unit/test_codeql_database.py`
- Test: `project/tests/unit/test_prepare_codeql.py`

**Interfaces:**
- Produces: `PreparationRequest`, `CodeQLDatabaseManifest`, `preparation_fingerprint()`, `validate_database_manifest()`, and CLI output containing the verified database path.
- Cache layout: `<cache-root>/databases/<sha256>/database` plus `<cache-root>/databases/<sha256>/manifest.json`.

- [ ] **Step 1: Write failing manifest and command tests**

```python
def test_preparation_fingerprint_changes_with_target_and_revision() -> None:
    base = fixture_request(targets=("services", "SystemUI"), revision="abc")
    assert preparation_fingerprint(base) == preparation_fingerprint(base)
    assert preparation_fingerprint(base) != preparation_fingerprint(
        replace(base, build_targets=("services",))
    )
    assert preparation_fingerprint(base) != preparation_fingerprint(
        replace(base, repositories=(replace(base.repositories[0], revision="def"),))
    )
```

```python
def test_build_command_uses_isolated_out_and_manual_java_kotlin_build(tmp_path: Path) -> None:
    request = fixture_request(cache_root=tmp_path)
    command = build_codeql_create_command(request, cache_key="a" * 64)
    assert "--language=java-kotlin" in command
    assert "--command" in command
    traced = command[command.index("--command") + 1]
    assert "OUT_DIR=" in traced
    assert "source build/envsetup.sh" in traced
    assert "lunch aosp_cf_x86_64_phone-userdebug" in traced
    assert "m -j8 services SystemUI" in traced
```

```python
def test_manifest_rejects_source_inventory_mismatch(tmp_path: Path) -> None:
    manifest = fixture_manifest(source_fingerprint="first")
    with pytest.raises(CodeQLDatabaseError, match="source_fingerprint"):
        validate_database_manifest(manifest, expected_source_fingerprint="second")
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_database.py tests/unit/test_prepare_codeql.py`

Expected: missing module failures.

- [ ] **Step 3: Implement immutable request/manifest models and safe command construction**

Use dataclasses with exact fields from design section 4. Hash canonical JSON with sorted keys and compact separators. Refuse an empty product, variant, target list, repository inventory, CodeQL version, or extractor version. Construct a temporary build script as an argument-safe file instead of interpolating untrusted values into `bash -c`; the script must export a cache-specific `OUT_DIR`, source `build/envsetup.sh`, run `lunch`, then `m` with the ordered targets.

- [ ] **Step 4: Implement prepare CLI and cache publication**

The Python CLI must:

```text
1. resolve CodeQL and print its version;
2. load enabled repository revision/inventory data from execution-plan.json;
3. compute the preliminary key;
4. build into <entry>.partial-<pid> with codeql database create;
5. run codeql database info --format=json;
6. record observed Java/Kotlin source coverage and finalized metadata;
7. write manifest.json atomically;
8. rename the complete partial entry to the final cache path;
9. validate an existing entry before reuse.
```

The shell wrapper only resolves `PROJECT_ROOT`, activates `.venv`, and executes `python -m scripts.prepare_codeql "$@"`.

- [ ] **Step 5: Run GREEN using a fake CodeQL executable**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_database.py tests/unit/test_prepare_codeql.py`

Expected: tests prove reuse, mismatch rejection, partial-cache cleanup, argument safety, and explicit product/variant requirements.

- [ ] **Step 6: Commit**

```bash
git add project/workspace/codeql_database.py project/scripts/prepare_codeql.py \
  project/scripts/prepare_codeql.sh project/config/codeql.toml \
  project/tests/unit/test_codeql_database.py project/tests/unit/test_prepare_codeql.py
git commit -m "feat: prepare verified CodeQL AOSP databases"
```

### Task 4: Version-Locked CodeQL Query Pack and Java/Kotlin Fixtures

**Files:**
- Create: `project/codeql/qlpack.yml`
- Create: `project/codeql/codeql-pack.lock.yml`
- Create: `project/codeql/lib/SystemServiceModels.qll`
- Create: `project/codeql/queries/CallSites.ql`
- Create: `project/codeql/queries/SystemServiceDataflow.ql`
- Create: `project/codeql/queries/SystemServiceGuards.ql`
- Create: `project/codeql/queries/BinderIdentity.ql`
- Create: `project/codeql/tests/call-sites/CallSites.qlref`
- Create: `project/codeql/tests/call-sites/CallSites.expected`
- Create: `project/codeql/tests/call-sites/Test.java`
- Create: `project/codeql/tests/call-sites/Test.kt`
- Create: `project/codeql/tests/security-flow/SecurityFlow.qlref`
- Create: `project/codeql/tests/security-flow/SecurityFlow.expected`
- Create: `project/codeql/tests/security-flow/Test.java`
- Create: `project/codeql/tests/security-flow/Test.kt`
- Test: `project/tests/unit/test_codeql_pack_contract.py`

**Interfaces:**
- Produces: tabular QL result schemas `semantic_definition_v1`, `call_site_v1`, `dataflow_path_v1`, `guard_v1`, and `identity_transition_v1`.
- QL pack dependency: an exact `codeql/java-all` version resolved and committed by `codeql pack install`.

- [ ] **Step 1: Write a Python contract test before QL files exist**

```python
def test_query_pack_declares_all_versioned_exports() -> None:
    pack = (ROOT / "codeql/qlpack.yml").read_text(encoding="utf-8")
    assert "name: android-context/java-kotlin-call-dataflow" in pack
    assert "version: 0.1.0" in pack
    for name in ("CallSites.ql", "SystemServiceDataflow.ql",
                 "SystemServiceGuards.ql", "BinderIdentity.ql"):
        text = (ROOT / "codeql/queries" / name).read_text(encoding="utf-8")
        assert "@kind table" in text
        assert "schema_version" in text
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_pack_contract.py`

Expected: missing `project/codeql/qlpack.yml`.

- [ ] **Step 3: Add pack metadata and shared System Service models**

`qlpack.yml` must declare:

```yaml
name: android-context/java-kotlin-call-dataflow
version: 0.1.0
libraryPathDependencies:
  codeql/java-all: "*"
```

Run `codeql pack install project/codeql` in WSL and commit the generated lock file. `SystemServiceModels.qll` must model exact qualified names for Binder caller UID/PID, permission enforcement/check APIs, AppOps APIs, cross-user checks, `clearCallingIdentity`, `restoreCallingIdentity`, configured Binder entry signatures, and configured sensitive sinks. Model predicates return the matching `Callable`/`Call`/argument rather than strings.

- [ ] **Step 4: Implement complete call-site export**

`CallSites.ql` must emit one row per source-backed `Call` and one definition row per source-backed `Callable`. Columns are fixed and decoded by name:

```text
schema_version, record_kind, language, package_name, declaring_type,
callable_kind, callable_name, erased_parameters, return_type,
repository_path, source_path, start_line, start_column, end_line, end_column,
caller_symbol_key, callee_symbol_key, dispatch_kind, relation_kind,
candidate_count, expression_text, unresolved_reason
```

Unique static/constructor/private/final targets use `must`; legal dynamic dispatch targets use `may`; a call without accepted targets emits one unresolved row with a reason. Fixture expectations must cover Java overloads, constructors, `super`, interface dispatch, anonymous classes, lambdas, Kotlin extension calls, and an unresolved dependency.

- [ ] **Step 5: Implement modular dataflow, guard, and identity queries**

Use the current modular API form:

```ql
module SystemServiceFlowConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) { ... }
  predicate isSink(DataFlow::Node sink) { ... }
}
module SystemServiceFlow = DataFlow::Global<SystemServiceFlowConfig>;
```

The dataflow table emits scenario, source/sink value identities, and ordered path nodes. The guard query emits only a configured check that dominates the sink with no modeled bypass. The identity query emits clear/token/restore sites and `paired_all_exits`; conditional or absent restoration emits a diagnostic record, not a safe pair. Positive and negative fixture rows must be exact in `.expected` files.

- [ ] **Step 6: Run pack contract and QL tests**

Run in WSL:

```bash
cd /home/ts/AndroidContextIntelligence/project
python -m pytest -q tests/unit/test_codeql_pack_contract.py
/opt/codeql/codeql pack install codeql
/opt/codeql/codeql test run codeql/tests
```

Expected: Python contract passes and all CodeQL tests pass with zero extra rows in negative fixtures.

- [ ] **Step 7: Commit**

```bash
git add project/codeql project/tests/unit/test_codeql_pack_contract.py
git commit -m "feat: add Java Kotlin CodeQL semantic query pack"
```

### Task 5: Query Runner, Result Cache, and Normalized Records

**Files:**
- Create: `project/collectors/codeql/__init__.py`
- Create: `project/collectors/codeql/model.py`
- Create: `project/collectors/codeql/decode.py`
- Create: `project/workspace/codeql_runner.py`
- Test: `project/tests/unit/test_codeql_decode.py`
- Test: `project/tests/unit/test_codeql_runner.py`

**Interfaces:**
- Produces immutable `DefinitionRecord`, `CallSiteRecord`, `CallTargetRecord`, `ProgramValueRecord`, `DataflowPathRecord`, `GuardRecord`, and `IdentityTransitionRecord`.
- Produces `run_queries(database, pack, output_dir, codeql_bin) -> QueryRunManifest` and normalized JSONL under staged `raw/codeql/normalized/`.

- [ ] **Step 1: Write failing decoder and cache-key tests**

```python
def test_decode_call_site_preserves_may_candidates_and_unresolved() -> None:
    records = decode_rows(load_fixture("call-sites.csv"))
    sites = [record for record in records if isinstance(record, CallSiteRecord)]
    assert sites[0].candidate_count == 2
    assert {target.relation_kind for target in sites[0].targets} == {"may"}
    assert sites[1].targets == ()
    assert sites[1].unresolved_reason == "missing_dependency"
```

```python
def test_query_result_key_includes_database_and_pack_lock() -> None:
    first = query_result_key("db-a", "lock-a", "CallSites", "1", {})
    assert first != query_result_key("db-b", "lock-a", "CallSites", "1", {})
    assert first != query_result_key("db-a", "lock-b", "CallSites", "1", {})
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_decode.py tests/unit/test_codeql_runner.py`

Expected: missing collector and runner modules.

- [ ] **Step 3: Implement strict named-column decoding**

Each decoder must reject an unknown schema version, a missing required column, invalid relation kind, negative source span, duplicate path ordinal, or invalid JSON. Canonical record content hashes exclude extraction timestamps and include stable endpoints, source span, scenario, query ID/version, and database fingerprint.

- [ ] **Step 4: Implement query execution and content-addressed result reuse**

For each query, run:

```text
codeql query run --database <db> --output <result.bqrs> <query.ql>
codeql bqrs decode --format=csv --entities=all --output <result.csv> <result.bqrs>
```

Cache by database fingerprint + pack lock hash + query ID + query version + canonical parameters. Write `query-run-manifest.json` atomically with raw hashes, normalized hashes, row counts, CodeQL version, and cache status. Never reuse a cache entry whose manifest or file hash does not validate.

- [ ] **Step 5: Run GREEN**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_decode.py tests/unit/test_codeql_runner.py`

Expected: all selected tests pass using a fake CodeQL binary and fixed CSV fixtures.

- [ ] **Step 6: Commit**

```bash
git add project/collectors/codeql project/workspace/codeql_runner.py \
  project/tests/unit/test_codeql_decode.py project/tests/unit/test_codeql_runner.py \
  project/tests/fixtures/codeql
git commit -m "feat: normalize and cache CodeQL query results"
```

### Task 6: Semantic Identity Reconciliation

**Files:**
- Create: `project/collectors/codeql/identity.py`
- Test: `project/tests/unit/test_codeql_identity.py`

**Interfaces:**
- Consumes: normalized `DefinitionRecord` values and existing logical `JAVA_METHOD` / `KOTLIN_METHOD` nodes.
- Produces: `ReconciliationResult(status, logical_method_ids, diagnostics)` with status `unique`, `ambiguous`, `unmatched`, or `synthetic`.

- [ ] **Step 1: Write failing identity tests**

```python
def test_erased_parameter_key_resolves_overload_uniquely(graph_db: Path) -> None:
    seed_method(graph_db, "JAVA_METHOD:demo.A#run(java.lang.String)")
    seed_method(graph_db, "JAVA_METHOD:demo.A#run(int)")
    result = reconcile_definition(
        graph_db,
        definition(language="java", declaring_type="demo.A", name="run",
                   erased_parameters=("java.lang.String",)),
    )
    assert result.status == "unique"
    assert result.logical_method_ids == (
        "JAVA_METHOD:demo.A#run(java.lang.String)",
    )
```

```python
def test_duplicate_logical_definitions_remain_ambiguous(graph_db: Path) -> None:
    seed_duplicate_methods(graph_db, "demo.A#run(java.lang.String)")
    result = reconcile_definition(graph_db, matching_definition())
    assert result.status == "ambiguous"
    assert len(result.logical_method_ids) == 2
```

```python
def test_lambda_is_retained_as_synthetic_not_user_method(graph_db: Path) -> None:
    result = reconcile_definition(graph_db, lambda_definition())
    assert result.status == "synthetic"
    assert result.logical_method_ids == ()
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_identity.py`

Expected: missing `collectors.codeql.identity`.

- [ ] **Step 3: Implement normalized semantic symbol keys and evidence scoring**

Normalize language, package, nested declaring type, callable kind, name, and erased parameter types. Do not use return type as overload identity. Use repository/source span, return type, and generic/compiler signature only to disambiguate otherwise compatible definitions. Exact duplicate logical candidates remain `ambiguous`; no name-only fallback is allowed.

- [ ] **Step 4: Run GREEN**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_identity.py`

Expected: overload, constructor, nested type, Kotlin extension, ambiguous, unmatched, and synthetic cases pass.

- [ ] **Step 5: Commit**

```bash
git add project/collectors/codeql/identity.py project/tests/unit/test_codeql_identity.py
git commit -m "feat: reconcile CodeQL and logical method identities"
```

### Task 7: Call-Site Materialization and Method-Level Projection

**Files:**
- Create: `project/collectors/codeql/materializer.py`
- Test: `project/tests/unit/test_codeql_call_materializer.py`
- Create: `project/queries/call_graph_summary.sql`

**Interfaces:**
- Consumes: normalized records, reconciliation results, run/evidence metadata.
- Produces: typed `semantic_definition`, `call_site`, `call_target`; nodes/edges `CALL_SITE`, `IN_METHOD`, `MUST_CALL`, `MAY_CALL`, and derived method `CALLS`.

- [ ] **Step 1: Write failing materializer tests**

```python
def test_unique_direct_call_creates_must_call_and_calls_projection(graph_db: Path) -> None:
    materialize_call_graph(graph_db, direct_call_fixture(), run_fixture())
    assert scalar(graph_db, "SELECT relation_kind FROM call_target") == "must"
    assert edge_count(graph_db, "MUST_CALL") == 1
    assert edge_count(graph_db, "CALLS") == 1
```

```python
def test_polymorphic_call_retains_all_may_targets(graph_db: Path) -> None:
    materialize_call_graph(graph_db, interface_call_fixture(), run_fixture())
    assert rows(graph_db, "SELECT relation_kind FROM call_target ORDER BY callee_method_id") == [
        ("may",), ("may",)
    ]
    assert edge_count(graph_db, "MAY_CALL") == 2
```

```python
def test_ambiguous_endpoint_is_diagnostic_not_accepted_edge(graph_db: Path) -> None:
    materialize_call_graph(graph_db, ambiguous_call_fixture(), run_fixture())
    assert scalar(graph_db, "SELECT resolution_status FROM call_site") == "ambiguous"
    assert scalar(graph_db, "SELECT COUNT(*) FROM call_target") == 0
    assert edge_count(graph_db, "MUST_CALL") == 0
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_call_materializer.py`

Expected: `materialize_call_graph` is absent.

- [ ] **Step 3: Implement deterministic IDs and typed writes**

Use stable hashes of database fingerprint, query version, repository/source span, caller semantic key, and expression hash. Insert the `CALL_SITE` node before typed rows and edges. Only a `unique` caller plus `unique` callee may create `call_target` and accepted graph edges. A zero-target site stays in `call_site` with a structured reason. Aggregate method `CALLS` by `(caller, callee, relation_kind)` and store supporting site count, total candidates, evidence IDs, and run ID in properties.

- [ ] **Step 4: Add query and run GREEN**

`call_graph_summary.sql` must group calls by relation kind, language, repository, and resolution status. Run:

```bash
cd project
python -m pytest -q tests/unit/test_codeql_call_materializer.py
```

Expected: all selected tests pass and `PRAGMA foreign_key_check` is empty.

- [ ] **Step 5: Commit**

```bash
git add project/collectors/codeql/materializer.py \
  project/tests/unit/test_codeql_call_materializer.py \
  project/queries/call_graph_summary.sql
git commit -m "feat: materialize precise Java Kotlin call sites"
```

### Task 8: Program Values, Security Dataflow, Guards, and Binder Identity

**Files:**
- Modify: `project/collectors/codeql/materializer.py`
- Test: `project/tests/unit/test_codeql_security_materializer.py`
- Create: `project/queries/system_service_security_traces.sql`

**Interfaces:**
- Produces: `PROGRAM_VALUE`, `DATAFLOW_PATH`, `SECURITY_TRACE` nodes; typed program values, ordered paths/steps/traces; `FLOW_SOURCE`, `FLOW_SINK`, `GUARDED_BY`, `IDENTITY_CLEARED_BY`, `IDENTITY_RESTORED_BY`, `TRACE_ENTRY`, and `TRACE_SINK` edges.

- [ ] **Step 1: Write failing fact-separation and ordering tests**

```python
def test_path_preserves_program_values_and_ordered_steps(graph_db: Path) -> None:
    materialize_security_facts(graph_db, security_fixture(), run_fixture())
    assert rows(graph_db, "SELECT ordinal, step_kind FROM dataflow_step ORDER BY ordinal") == [
        (0, "source"), (1, "argument"), (2, "return"), (3, "sink")
    ]
    assert edge_count(graph_db, "FLOW_SOURCE") == 1
    assert edge_count(graph_db, "FLOW_SINK") == 1
```

```python
def test_guard_is_not_materialized_as_dataflow(graph_db: Path) -> None:
    materialize_security_facts(graph_db, guarded_sink_fixture(), run_fixture())
    assert edge_count(graph_db, "GUARDED_BY") == 1
    assert scalar(graph_db, "SELECT COUNT(*) FROM dataflow_step WHERE step_kind='guard'") == 0
```

```python
def test_unpaired_identity_restore_remains_diagnostic(graph_db: Path) -> None:
    materialize_security_facts(graph_db, unpaired_identity_fixture(), run_fixture())
    assert edge_count(graph_db, "IDENTITY_CLEARED_BY") == 1
    assert edge_count(graph_db, "IDENTITY_RESTORED_BY") == 0
    assert scalar(graph_db, "SELECT status FROM security_trace") == "identity_unpaired"
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_security_materializer.py`

Expected: security materialization entrypoint is missing.

- [ ] **Step 3: Implement typed value and path materialization**

Create stable value IDs from owner method, kind, parameter index, source span, type, and expression hash. Reject duplicate or non-contiguous ordinals. A `dataflow_path` requires accepted source/sink values and one extraction evidence row. `security_trace_step` references exactly one of call site, dataflow path, guard call site, or identity call site per ordinal using a table check constraint.

- [ ] **Step 4: Compose security traces without semantic relabeling**

Compose an entry, sink, zero or more guards, zero or more identity transitions, and zero or more value paths. `GUARDED_BY` requires query evidence marked dominance-proven. `IDENTITY_RESTORED_BY` requires `paired_all_exits=true`. Diagnostics remain typed trace statuses and never create safe edges.

- [ ] **Step 5: Run GREEN**

Run: `cd project && python -m pytest -q tests/unit/test_codeql_security_materializer.py`

Expected: positive paths, branch bypass negatives, cross-user checks, AppOps checks, complete `finally` restoration, incomplete restoration, and Kotlin entry fixtures pass.

- [ ] **Step 6: Commit**

```bash
git add project/collectors/codeql/materializer.py \
  project/tests/unit/test_codeql_security_materializer.py \
  project/queries/system_service_security_traces.sql
git commit -m "feat: materialize System Service security traces"
```

### Task 9: Git-Managed Fact Corrections and Effective View

**Files:**
- Create: `project/collectors/codeql/corrections.py`
- Create: `project/config/corrections/.gitkeep`
- Test: `project/tests/unit/test_fact_corrections.py`

**Interfaces:**
- Produces: `load_corrections(directory)`, `validate_corrections(...)`, `apply_corrections(...)`, immutable application report, effective statuses, `SUPERSEDES` and `CONTRADICTS` edges.

- [ ] **Step 1: Write failing correction lifecycle tests**

```python
def test_suppress_retains_raw_fact_and_hides_effective_fact(graph_db: Path, tmp_path: Path) -> None:
    fact = seed_call_fact(graph_db)
    correction = approved_correction(action="suppress", target=fact.uri,
                                     expected_hash=fact.content_hash)
    apply_corrections(graph_db, (correction,), source_revision="abc")
    assert scalar(graph_db, "SELECT status FROM edge WHERE edge_id=?", (fact.edge_id,)) == "active"
    assert effective_fact_count(graph_db, fact.uri) == 0
```

```python
def test_revision_or_hash_mismatch_marks_correction_stale(graph_db: Path) -> None:
    correction = approved_correction(expected_hash="0" * 64,
                                     applicable_source_revision="abc")
    report = apply_corrections(graph_db, (correction,), source_revision="abc")
    assert report.applications[0].status == "stale"
```

```python
@pytest.mark.parametrize("action", ["replace", "add"])
def test_replacement_payload_must_be_complete(action: str) -> None:
    with pytest.raises(CorrectionError, match="replacement payload"):
        parse_correction(incomplete_correction(action))
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/unit/test_fact_corrections.py`

Expected: missing corrections module.

- [ ] **Step 3: Implement strict TOML schema and deterministic application**

Require every field in design section 9.1, reject duplicate IDs, unknown keys/actions, missing approval, invalid fact URIs, and replacement payloads that fail the target typed schema. Apply in sorted `correction_id` order. `annotate` may change confidence/explanation only. `suppress` alters the effective SQL view but not raw rows. `replace` inserts a reviewed fact and `SUPERSEDES`; `add` inserts reviewed external evidence and marks origin `correction`.

- [ ] **Step 4: Run GREEN**

Run: `cd project && python -m pytest -q tests/unit/test_fact_corrections.py`

Expected: all four actions, approval validation, stale binding, conflict detection, idempotent replay, and raw-fact preservation pass.

- [ ] **Step 5: Commit**

```bash
git add project/collectors/codeql/corrections.py \
  project/config/corrections/.gitkeep \
  project/tests/unit/test_fact_corrections.py
git commit -m "feat: add reviewed semantic fact corrections"
```

### Task 10: Validation, Semantic Fingerprints, and Graph Diff

**Files:**
- Create: `project/workspace/call_dataflow_validation.py`
- Modify: `project/scripts/graph_fingerprint.py`
- Create: `project/scripts/graph_diff.py`
- Test: `project/tests/unit/test_call_dataflow_validation.py`
- Modify: `project/tests/unit/test_graph_fingerprint.py`
- Test: `project/tests/unit/test_graph_diff.py`

**Interfaces:**
- Produces: `validate_call_dataflow(...) -> ValidationReport`; fingerprints for call/dataflow/security/correction/effective/whole graph; `graph_diff.py --before --after` JSON and text reports.

- [ ] **Step 1: Write failing structural gate tests**

```python
def test_validator_rejects_accepted_target_with_ambiguous_endpoint(graph_db: Path) -> None:
    seed_ambiguous_accepted_target(graph_db)
    with pytest.raises(CallDataflowValidationError, match="ambiguous endpoint"):
        validate_call_dataflow(graph_db, require_aosp_evidence=False)
```

```python
def test_validator_requires_contiguous_path_steps(graph_db: Path) -> None:
    seed_path_steps(graph_db, ordinals=(0, 2))
    with pytest.raises(CallDataflowValidationError, match="non-contiguous"):
        validate_call_dataflow(graph_db, require_aosp_evidence=False)
```

```python
def test_semantic_fingerprint_excludes_run_timestamps(graph_db: Path) -> None:
    first = graph_semantic_fingerprints(graph_db)
    update_extraction_timestamps(graph_db)
    assert graph_semantic_fingerprints(graph_db) == first
```

```python
def test_graph_diff_classifies_suppressed_and_superseded(before: Path, after: Path) -> None:
    diff = compare_graphs(before, after)
    assert diff.counts["suppressed"] == 1
    assert diff.counts["superseded"] == 1
```

- [ ] **Step 2: Run RED**

Run:

```bash
cd project
python -m pytest -q \
  tests/unit/test_call_dataflow_validation.py \
  tests/unit/test_graph_fingerprint.py \
  tests/unit/test_graph_diff.py
```

Expected: missing validation/diff APIs and missing typed-table fingerprint coverage.

- [ ] **Step 3: Implement all hard structural and quality gates**

Validate foreign keys, typed checks, accepted unique endpoints, source spans, evidence, contiguous steps, source/sink presence, query identity, duplicate content hashes, identity pairing semantics, correction status, and classification of every call site. Compute reconciliation numerator/denominator and enforce 95% only when `--require-aosp-evidence` is active. Strong-evidence validation uses checked configuration entries for exact AMS, PMS, and Kotlin SystemUI source/method/scenario IDs plus committed negative cases.

- [ ] **Step 4: Extend fingerprints and graph diff**

Hash stable ordered rows from all semantic typed tables, excluding timestamps, local absolute cache paths, and extraction run IDs when a stable database fingerprint exists. Produce named digests:

```text
call_graph
interprocedural_dataflow
security_trace
correction_effective_view
whole_graph
```

Diff typed stable IDs/content hashes and classify `added`, `removed`, `changed`, `suppressed`, `superseded`, and `stale`.

- [ ] **Step 5: Run GREEN**

Run the same selected tests. Expected: all pass, including deterministic insertion-order and timestamp-change cases.

- [ ] **Step 6: Commit**

```bash
git add project/workspace/call_dataflow_validation.py \
  project/scripts/graph_fingerprint.py project/scripts/graph_diff.py \
  project/tests/unit/test_call_dataflow_validation.py \
  project/tests/unit/test_graph_fingerprint.py project/tests/unit/test_graph_diff.py
git commit -m "feat: validate and diff semantic graph facts"
```

### Task 11: Atomic Rebuild, Provenance, History, and Failure Isolation

**Files:**
- Modify: `project/scripts/rebuild_all.sh`
- Modify: `project/workspace/provenance.py`
- Modify: `project/workspace/build_publish.py`
- Create: `project/workspace/codeql_import.py`
- Modify: `project/tests/integration/test_atomic_rebuild.py`
- Create: `project/tests/integration/test_codeql_atomic_rebuild.py`

**Interfaces:**
- CLI adds `--codeql-db PATH`, `--corrections-dir PATH`, `--retain-history`, and existing `--strict-capability` support.
- Default rebuild without CodeQL publishes the base graph with new capabilities degraded/unsupported; strict call/dataflow capability fails before publication.

- [ ] **Step 1: Write failing script contract and failure-isolation tests**

```python
def test_codeql_import_occurs_before_validation_and_publication() -> None:
    script = CANONICAL_SCRIPT.read_text(encoding="utf-8")
    assert "--codeql-db" in script
    assert script.index("workspace.codeql_import") < script.index(
        "workspace.call_dataflow_validation"
    )
    assert script.index("workspace.call_dataflow_validation") < script.index(
        "workspace.build_publish prepare"
    )
```

```python
def test_codeql_validation_failure_preserves_live_database(project: Path) -> None:
    before = _checksum(project / "data/android_context.db")
    result = _run(project, "--codeql-db", str(fake_db(project)),
                  FORCE_CODEQL_VALIDATION_FAILURE="1")
    assert result.returncode != 0
    assert _checksum(project / "data/android_context.db") == before
```

```python
def test_default_rebuild_without_codeql_still_publishes_base_graph(project: Path) -> None:
    result = _run(project)
    assert result.returncode == 0
    report = json.loads((project / "data/workspace/capability-report.json").read_text())
    assert any(item["capability"] == "call_graph" and item["status"] == "degraded"
               for item in report)
```

- [ ] **Step 2: Run RED**

Run: `cd project && python -m pytest -q tests/integration/test_atomic_rebuild.py tests/integration/test_codeql_atomic_rebuild.py`

Expected: CodeQL CLI contract and integration module are absent.

- [ ] **Step 3: Implement one semantic import orchestrator**

`workspace.codeql_import` must validate the database manifest, run or reuse queries, insert extraction/evidence metadata, reconcile identities, materialize call/security facts, replay corrections, and write `raw/codeql/call-dataflow-report.json` plus `workspace/correction-application-report.json`. It receives only staged paths and must never open the live database for writing.

- [ ] **Step 4: Integrate default degradation and strict rejection**

If `--codeql-db` is absent, write a structured skipped report and let runtime coverage mark both capabilities degraded. If strict capability is `call_graph` or `interprocedural_dataflow`, absence/mismatch/import failure exits nonzero and the staging cleanup path preserves the live batch. Run corrections after raw facts and before validation. Run call/dataflow validation, collision validation, runtime coverage, provenance, and FK checks before publication.

- [ ] **Step 5: Record provenance and optional immutable history**

Add CodeQL database manifest identity, CLI/extractor versions, query pack lock hash, result manifest hash, correction directory hash, and semantic fingerprints to provenance and build manifest. With `--retain-history`, copy the verified build manifest, capability/quality/correction reports, query hashes, and fingerprints to `data/history/<build-id>/`; copy SQLite only when an explicit `--retain-history-database` flag is present.

- [ ] **Step 6: Run GREEN and the complete Python regression suite**

Run:

```bash
cd project
python -m pytest -q
cd ..
python -m pytest -q
```

Expected: project and root suites pass; forced CodeQL/query/correction/validation failures leave the live database and reports unchanged.

- [ ] **Step 7: Commit**

```bash
git add project/scripts/rebuild_all.sh project/workspace/provenance.py \
  project/workspace/build_publish.py project/workspace/codeql_import.py \
  project/tests/integration/test_atomic_rebuild.py \
  project/tests/integration/test_codeql_atomic_rebuild.py
git commit -m "feat: publish CodeQL graph facts atomically"
```

### Task 12: Deployment Payload, Documentation, and Real-AOSP Acceptance

**Files:**
- Modify: `scripts/project_payload.py`
- Modify: `tests/test_project_payload.py`
- Modify: `project/INSTALLATION_MANIFEST.txt`
- Modify: `project/README.md`
- Modify: `README.md`
- Modify: `doc/README.md`
- Create: `doc/reviews/2026-08-20-java-kotlin-call-dataflow-v01-acceptance.md`
- Modify: `tests/test_documentation_contract.py`

**Interfaces:**
- Adds `codeql` to the canonical deployable payload.
- Documents Windows source checkout, WSL generated target, heavy preparation, normal rebuild, strict gates, corrections, graph queries, diff, limitations, and recovery.

- [ ] **Step 1: Write failing deployment and documentation contract tests**

```python
def test_payload_contract_includes_codeql_pack() -> None:
    assert "codeql" in PAYLOAD_DIRECTORIES
    paths = relative_paths(PROJECT_ROOT)
    assert "codeql/qlpack.yml" in paths
    assert "codeql/queries/CallSites.ql" in paths
```

```python
def test_readmes_document_call_dataflow_workflow() -> None:
    combined = README.read_text(encoding="utf-8") + PROJECT_README.read_text(encoding="utf-8")
    for token in ("prepare_codeql.sh", "--codeql-db", "call_graph",
                  "interprocedural_dataflow", "MUST_CALL", "MAY_CALL",
                  "FACT_CORRECTION", "graph_diff.py"):
        assert token in combined
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest -q tests/test_project_payload.py tests/test_documentation_contract.py`

Expected: `codeql` is not in the payload and workflow documentation is absent.

- [ ] **Step 3: Update canonical deployment and operational documentation**

Document these exact workflows:

```bash
# Windows canonical source -> WSL generated deployment
cd /home/ts/AndroidContextIntelligence
AOSP_ROOT=/home/ts/aosp \
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --upgrade

# Heavy CodeQL preparation
cd /home/ts/android-context-intelligence
bash scripts/prepare_codeql.sh \
  --aosp-root /home/ts/aosp \
  --codeql-bin /opt/codeql/codeql \
  --product aosp_cf_x86_64_phone \
  --variant userdebug \
  --build-target services \
  --build-target SystemUI

# Normal verified rebuild
bash scripts/rebuild_all.sh \
  --codeql-db /path/printed/by/prepare_codeql.sh \
  --strict-capability call_graph \
  --retain-history
```

Also document that clean AOSP does not receive `android-context-current`; only the canonical `project/` payload is installed by `setup.sh`. Explain default degraded behavior, explicit strict gates, cache invalidation, correction review, queries, and unsupported C/C++/Rust/native Binder scope.

- [ ] **Step 4: Run deployment and documentation GREEN**

Run:

```bash
python -m pytest -q tests/test_project_payload.py tests/test_documentation_contract.py
python scripts/verify_project_install.py --help
```

Expected: tests pass and the verifier remains available.

- [ ] **Step 5: Install the feature into WSL and create a real CodeQL database**

Run from WSL source checkout:

```bash
cd /home/ts/AndroidContextIntelligence
AOSP_ROOT=/home/ts/aosp \
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --upgrade

cd /home/ts/android-context-intelligence
bash scripts/prepare_codeql.sh \
  --aosp-root /home/ts/aosp \
  --codeql-bin /opt/codeql/codeql \
  --product aosp_cf_x86_64_phone \
  --variant userdebug \
  --build-target services \
  --build-target SystemUI \
  --threads 8 \
  --ram-mb 24576 \
  --cache-root /home/ts/.cache/android-context-codeql
```

Expected: a verified cache path is printed; the manifest reports `java-kotlin`, the exact targets, nonzero observed Java and Kotlin files, and matching source revisions/inventories.

- [ ] **Step 6: Run the two-build real-AOSP acceptance gate**

Run twice with identical inputs and separate retained history IDs:

```bash
cd /home/ts/android-context-intelligence
bash scripts/rebuild_all.sh \
  --codeql-db /home/ts/.cache/android-context-codeql/databases/<verified-key>/database \
  --strict-capability call_graph \
  --retain-history

bash scripts/rebuild_all.sh \
  --codeql-db /home/ts/.cache/android-context-codeql/databases/<verified-key>/database \
  --strict-capability interprocedural_dataflow \
  --retain-history
```

The operator substitutes the exact verified key printed in Step 5. Expected gates:

```text
foreign_key_check: PASS
identity reconciliation >= 95%
accepted ambiguous/missing endpoints = 0
negative fixture false positives = 0
AMS strong evidence = PASS
PMS strong evidence = PASS
Kotlin SystemUI strong evidence = PASS
call_graph fingerprint equal across identical builds
interprocedural_dataflow fingerprint equal across identical builds
security_trace fingerprint equal across identical builds
whole_graph fingerprint equal across identical builds
```

- [ ] **Step 7: Capture acceptance evidence and run final verification**

Write exact CodeQL version, database fingerprint, AOSP revisions/dirty states, product/variant/targets, observed Java/Kotlin counts, reconciliation metrics, call relation counts, path/guard/identity counts, correction report, five fingerprints, and representative AMS/PMS/SystemUI query output into the acceptance review. Then run:

```bash
cd /home/ts/AndroidContextIntelligence
python -m pytest -q
PROJECT_ROOT=/home/ts/android-context-intelligence bash ./setup.sh --verify-only
git diff --check
git status --short
```

Expected: root suite passes, installation verification passes, diff check is clean, and only the acceptance review is uncommitted before the final commit.

- [ ] **Step 8: Commit acceptance**

```bash
git add scripts/project_payload.py tests/test_project_payload.py \
  project/INSTALLATION_MANIFEST.txt project/README.md README.md doc/README.md \
  doc/reviews/2026-08-20-java-kotlin-call-dataflow-v01-acceptance.md \
  tests/test_documentation_contract.py
git commit -m "docs: accept Java Kotlin call dataflow graph"
```

## Final Branch Gate and Main Integration

- [ ] Run `git status --short` and require no output.
- [ ] Run `python -m pytest -q` at repository root and record the result.
- [ ] Run `cd project && python -m pytest -q` and record the result.
- [ ] Run `git diff main...HEAD --check` and require no output.
- [ ] Run `git log --oneline main..HEAD` and verify every task has one reviewable commit.
- [ ] Use `superpowers:requesting-code-review` for a specification and code-quality review.
- [ ] Address accepted review findings with focused commits and rerun all affected gates.
- [ ] Use `superpowers:verification-before-completion` before claiming completion.
- [ ] Use `superpowers:finishing-a-development-branch`, merge the verified branch locally into `main`, rerun the root and project suites on `main`, and remove only the now-merged clean feature worktree.

## Plan Self-Review Record

- Spec coverage: all confirmed design sections map to Tasks 1-12, including CodeQL lifecycle, semantic identity, typed schema, uncertainty, System Service flow/guards/identity, corrections, validation, atomic publication, history/diffs, deployment, and real-AOSP acceptance.
- Placeholder scan: the plan contains no deferred implementation marker; the `<verified-key>` token is an operator-substituted value emitted by the immediately preceding preparation command, not an unspecified design decision.
- Type consistency: `ParserSpec.implementation_for`, typed table names, normalized record types, reconciliation statuses, relation kinds, correction actions, and validation/fingerprint names are defined before later consumption.
