# Atomic Database Rebuild v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the canonical multi-repository rebuild publish one verified database/report batch without damaging the last verified batch when parsing, validation, publication, or the host process fails.

**Architecture:** A new `workspace.build_publish` module creates build-specific staging and rollback directories, records a durable publication journal, checkpoints SQLite WAL state, publishes reports first, and atomically replaces the database last. `scripts/rebuild_all.sh` orchestrates the staged paths and failure policy; the self-contained multi-repository installer embeds the tested module, tests, and canonical script.

**Tech Stack:** Python 3.11 standard library, Bash 5, `flock`, SQLite 3, pytest, Universal Ctags.

## Global Constraints

- Preserve the stable consumer paths `data/android_context.db`, `data/workspace`, and `data/raw`.
- Stage the database, workspace reports, raw reports, and build manifest under one `data/staging/<build-id>` directory.
- Treat the SQLite main-file replacement as the final commit point.
- Checkpoint staged WAL content and publish a single-file DELETE-journal database.
- Refuse publication if the live database remains busy or has live WAL/SHM sidecars.
- Default failure handling deletes staging; `--keep-failed-db` retains the complete failed batch.
- A pre-commit failure must preserve the byte checksum of the previous database.
- A publication interruption must be recoverable and recovery must be idempotent.
- `--discover-only` and `--plan-only` must not create a staged database, but must hold the common lock while updating workspace reports.
- A second full rebuild must fail immediately when another rebuild holds the lock.
- Clean installation must continue to require only the existing five root shell scripts.
- Every logical change is committed separately on Git branch `main`.

---

### Task 1: Add the build-batch lifecycle model

**Files:**
- Create: `android-context-current/workspace/__init__.py`
- Create: `android-context-current/workspace/build_publish.py`
- Create: `android-context-current/tests/unit/test_build_publish.py`

**Interfaces:**
- Produces: `BuildBatch`
- Produces: `begin_build(data_root: Path, build_id: str | None = None) -> BuildBatch`
- Produces: `load_build_batch(staging_root: Path) -> BuildBatch`
- Produces: `cleanup_failed_build(batch: BuildBatch, keep: bool) -> Path | None`
- Produces: `write_build_manifest(batch: BuildBatch, source_config: Path, started_at: str, verified_at: str) -> Path`
- Consumes later: the canonical rebuild and publication commands.

- [ ] **Step 1: Write failing lifecycle tests**

Create tests that express the required directory layout and failure cleanup before creating the production module:

```python
def test_begin_build_creates_isolated_batch(tmp_path: Path) -> None:
    live_db = tmp_path / "android_context.db"
    live_db.write_bytes(b"verified")

    batch = begin_build(tmp_path, build_id="build-1")

    assert batch.staging_root == tmp_path / "staging/build-1"
    assert batch.database == batch.staging_root / "android_context.db"
    assert batch.workspace.is_dir()
    assert (batch.raw / "ctags").is_dir()
    assert live_db.read_bytes() == b"verified"


def test_failed_build_is_deleted_by_default(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    batch.database.write_bytes(b"partial")

    retained = cleanup_failed_build(batch, keep=False)

    assert retained is None
    assert not batch.staging_root.exists()
    assert cleanup_failed_build(batch, keep=False) is None


def test_keep_failed_build_preserves_complete_batch(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    batch.database.write_bytes(b"partial")

    retained = cleanup_failed_build(batch, keep=True)

    assert retained == batch.staging_root.resolve()
    assert batch.database.read_bytes() == b"partial"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd /mnt/d/AndroidContextIntelligence/android-context-current
python -m pytest -q tests/unit/test_build_publish.py
```

Expected: collection fails because `workspace.build_publish` does not exist.

- [ ] **Step 3: Implement the lifecycle model**

Implement this public shape:

```python
@dataclass(frozen=True)
class BuildBatch:
    data_root: Path
    build_id: str
    staging_root: Path
    database: Path
    workspace: Path
    raw: Path
    rollback_root: Path
    journal: Path


def begin_build(data_root: Path, build_id: str | None = None) -> BuildBatch:
    resolved = data_root.resolve()
    identity = build_id or generate_build_id()
    staging = resolved / "staging" / identity
    staging.mkdir(parents=True, exist_ok=False)
    workspace = staging / "workspace"
    raw = staging / "raw"
    workspace.mkdir()
    for name in ("ctags", "aidl", "inheritance", "service"):
        (raw / name).mkdir(parents=True)
    return BuildBatch(
        data_root=resolved,
        build_id=identity,
        staging_root=staging,
        database=staging / "android_context.db",
        workspace=workspace,
        raw=raw,
        rollback_root=resolved / "rollback" / identity,
        journal=resolved / ".publish-journal.json",
    )
```

Use UTC time, process ID, and `secrets.token_hex(4)` in `generate_build_id()`. Reject build IDs containing path separators or `..`.

- [ ] **Step 4: Run lifecycle tests and the existing unit suite**

Run:

```bash
python -m pytest -q tests/unit/test_build_publish.py tests/unit
```

Expected: all tests pass.

- [ ] **Step 5: Commit the lifecycle model**

```bash
git add android-context-current/workspace android-context-current/tests/unit/test_build_publish.py
git commit -m "feat: add graph build batch lifecycle"
```

---

### Task 2: Add graph build identity and SQLite WAL safety

**Files:**
- Modify: `android-context-current/workspace/build_publish.py`
- Modify: `android-context-current/tests/unit/test_build_publish.py`

**Interfaces:**
- Produces: `record_graph_build(batch: BuildBatch, source_config: Path, started_at: str, verified_at: str) -> None`
- Produces: `read_graph_build_id(database: Path) -> str | None`
- Produces: `prepare_staged_database(database: Path) -> None`
- Produces: `ensure_live_database_quiescent(database: Path) -> None`
- Requires: the existing `node` schema and `graph.writer.GraphWriter`.

- [ ] **Step 1: Write failing build-ID and WAL tests**

Use a minimal real SQLite node table fixture. Assert that:

```python
def create_full_node_schema(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE node (
          node_id TEXT PRIMARY KEY,
          node_type TEXT NOT NULL,
          qualified_name TEXT,
          display_name TEXT NOT NULL,
          properties_json TEXT NOT NULL DEFAULT '{}',
          source_path TEXT,
          line_start INTEGER,
          line_end INTEGER,
          source_revision TEXT,
          extractor TEXT NOT NULL,
          extractor_version TEXT NOT NULL,
          content_hash TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          updated_at TEXT NOT NULL
        );
    """)
    connection.commit()
    connection.close()


def test_records_matching_database_and_manifest_build_ids(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    source_config = tmp_path / "source_roots.toml"
    source_config.write_text("[workspace]\n", encoding="utf-8")
    create_full_node_schema(batch.database)

    record_graph_build(
        batch,
        source_config,
        "2026-07-16T15:00:00Z",
        "2026-07-16T15:01:00Z",
    )
    write_build_manifest(
        batch,
        source_config,
        "2026-07-16T15:00:00Z",
        "2026-07-16T15:01:00Z",
    )

    assert read_graph_build_id(batch.database) == batch.build_id
    manifest = json.loads((batch.workspace / "build-manifest.json").read_text())
    assert manifest["build_id"] == batch.build_id
    assert manifest["status"] == "verified"
```

Create a WAL-mode staged database, commit data, call `prepare_staged_database()`, then assert:

```python
def test_prepare_staged_database_removes_wal_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "staged.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sample(value TEXT)")
    connection.execute("INSERT INTO sample VALUES('value')")
    connection.commit()
    connection.close()

    prepare_staged_database(database)

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    connection.close()
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def test_busy_live_database_rejects_publication(tmp_path: Path) -> None:
    database = tmp_path / "android_context.db"
    active = sqlite3.connect(database)
    active.execute("PRAGMA journal_mode=WAL")
    active.execute("CREATE TABLE sample(value TEXT)")
    active.execute("INSERT INTO sample VALUES('active')")
    active.commit()
    sidecar = Path(f"{database}-wal")
    assert sidecar.exists()

    with pytest.raises(PublicationError, match="sidecar"):
        ensure_live_database_quiescent(database)

    assert sidecar.exists()
    active.close()
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python -m pytest -q \
  tests/unit/test_build_publish.py::test_records_matching_database_and_manifest_build_ids \
  tests/unit/test_build_publish.py::test_prepare_staged_database_removes_wal_sidecars \
  tests/unit/test_build_publish.py::test_busy_live_database_rejects_publication
```

Expected: failures because the functions do not exist.

- [ ] **Step 3: Implement build identity and WAL preparation**

Use `GraphWriter.upsert_node()` with:

```python
Node(
    node_id=f"GRAPH_BUILD:{batch.build_id}",
    node_type="GRAPH_BUILD",
    qualified_name=batch.build_id,
    display_name=batch.build_id,
    properties={
        "source_config": str(source_config.resolve()),
        "started_at": started_at,
        "verified_at": verified_at,
    },
    extractor="build_publish",
)
```

`prepare_staged_database()` must execute, in order:

```sql
PRAGMA wal_checkpoint(TRUNCATE);
PRAGMA journal_mode=DELETE;
```

Close the connection before checking sidecars. `ensure_live_database_quiescent()` must use a short SQLite timeout, checkpoint the live database when it exists, and reject publication when checkpoint reports busy or either sidecar remains.

- [ ] **Step 4: Run all publisher tests**

```bash
python -m pytest -q tests/unit/test_build_publish.py
```

Expected: all publisher tests pass.

- [ ] **Step 5: Commit build identity and WAL safety**

```bash
git add android-context-current/workspace/build_publish.py android-context-current/tests/unit/test_build_publish.py
git commit -m "feat: prepare sqlite graph builds for atomic publish"
```

---

### Task 3: Implement journaled publication and recovery

**Files:**
- Modify: `android-context-current/workspace/build_publish.py`
- Modify: `android-context-current/tests/unit/test_build_publish.py`

**Interfaces:**
- Produces: `publish_build(batch: BuildBatch, *, replace_database: Callable[[Path, Path], None] = os.replace) -> None`
- Produces: `recover_publication(data_root: Path) -> str`
- Produces: `PublicationError`
- Produces CLI commands: `begin`, `prepare`, `publish`, `fail`, `recover`.
- Journal schema: `build_id`, `staging_root`, `rollback_root`, `phase`.

- [ ] **Step 1: Write failing publication tests**

Use real SQLite files and report directories:

```python
def seed_database(path: Path, build_id: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE node(node_type TEXT, qualified_name TEXT)")
    connection.execute("INSERT INTO node VALUES('GRAPH_BUILD', ?)", (build_id,))
    connection.commit()
    connection.close()


def seed_reports(root: Path, marker: str) -> None:
    for name in ("workspace", "raw"):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "marker.txt").write_text(marker, encoding="utf-8")


def ready_batch(data_root: Path, build_id: str = "new") -> BuildBatch:
    batch = begin_build(data_root, build_id=build_id)
    seed_database(batch.database, build_id)
    (batch.workspace / "marker.txt").write_text("new", encoding="utf-8")
    (batch.workspace / "build-manifest.json").write_text(
        json.dumps({"build_id": build_id, "status": "verified"}),
        encoding="utf-8",
    )
    (batch.raw / "marker.txt").write_text("new", encoding="utf-8")
    return batch


def test_publish_replaces_reports_and_database_as_one_batch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)

    publish_build(batch)

    assert read_graph_build_id(data / "android_context.db") == "new"
    assert (data / "workspace/marker.txt").read_text() == "new"
    assert (data / "raw/marker.txt").read_text() == "new"
    assert not batch.journal.exists()


def test_precommit_failure_restores_old_batch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    before = hashlib.sha256((data / "android_context.db").read_bytes()).hexdigest()
    batch = ready_batch(data)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected database replacement failure")

    with pytest.raises(OSError, match="injected"):
        publish_build(batch, replace_database=fail_replace)

    after = hashlib.sha256((data / "android_context.db").read_bytes()).hexdigest()
    assert after == before
    assert (data / "workspace/marker.txt").read_text() == "old"
    assert (batch.workspace / "marker.txt").read_text() == "new"
    assert not batch.journal.exists()


def test_publish_rejects_mismatched_build_identity_before_moving_live_files(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data, build_id="new")
    (batch.workspace / "build-manifest.json").write_text(
        json.dumps({"build_id": "different", "status": "verified"}),
        encoding="utf-8",
    )

    with pytest.raises(PublicationError, match="build identity"):
        publish_build(batch)

    assert read_graph_build_id(data / "android_context.db") == "old"
    assert (data / "workspace/marker.txt").read_text() == "old"


def simulate_reports_published(batch: BuildBatch) -> None:
    batch.rollback_root.mkdir(parents=True)
    os.replace(batch.data_root / "workspace", batch.rollback_root / "workspace")
    os.replace(batch.data_root / "raw", batch.rollback_root / "raw")
    os.replace(batch.workspace, batch.data_root / "workspace")
    os.replace(batch.raw, batch.data_root / "raw")
    batch.journal.write_text(json.dumps({
        "build_id": batch.build_id,
        "staging_root": str(batch.staging_root),
        "rollback_root": str(batch.rollback_root),
        "phase": "new_reports_published",
    }), encoding="utf-8")


def test_recovery_rolls_back_when_database_has_old_build_id(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    simulate_reports_published(batch)

    assert recover_publication(data) == "rolled_back"
    assert read_graph_build_id(data / "android_context.db") == "old"
    assert (data / "workspace/marker.txt").read_text() == "old"
    assert (batch.workspace / "marker.txt").read_text() == "new"


def test_recovery_finishes_cleanup_when_database_has_new_build_id(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    seed_database(data / "android_context.db", "old")
    seed_reports(data, "old")
    batch = ready_batch(data)
    simulate_reports_published(batch)
    os.replace(batch.database, data / "android_context.db")

    assert recover_publication(data) == "committed"
    assert read_graph_build_id(data / "android_context.db") == "new"
    assert (data / "workspace/marker.txt").read_text() == "new"
    assert not batch.rollback_root.exists()
    assert not batch.journal.exists()
    assert recover_publication(data) == "no_journal"


def test_first_build_failure_leaves_live_database_absent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    batch = ready_batch(data)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected first-build failure")

    with pytest.raises(OSError, match="first-build"):
        publish_build(batch, replace_database=fail_replace)

    assert not (data / "android_context.db").exists()
```

Inject a failure immediately before `os.replace(staged_db, live_db)` through a private `_replace_database` callable passed to `publish_build()` only from tests. Do not add a production CLI flag for failure injection.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python -m pytest -q tests/unit/test_build_publish.py -k "publish or recovery or first_build"
```

Expected: failures because journaled publication is absent.

- [ ] **Step 3: Implement durable journal writes**

Write JSON to a sibling temporary file, flush and `os.fsync()` it, call `os.replace(temp, journal)`, then fsync the parent directory. Use the same helper for every journal phase transition.

Publication phases are:

```text
prepared
old_reports_backed_up
new_reports_published
database_committed
```

- [ ] **Step 4: Implement publish and recovery**

Before moving reports, call `ensure_live_database_quiescent()`.

Before moving reports, also require:

```python
read_graph_build_id(batch.database) == batch.build_id
json.loads((batch.workspace / "build-manifest.json").read_text())["build_id"] == batch.build_id
```

Raise `PublicationError("build identity mismatch")` without touching live artifacts when either comparison fails.

On pre-commit recovery:

1. move newly published reports back into staging when present;
2. restore rollback reports to their live locations;
3. leave the old live database untouched.

On recovery, do not trust `phase` to decide whether the database committed. Call `read_graph_build_id(data/android_context.db)` and compare it with the journal build ID. A match means commit occurred and cleanup must finish; a mismatch means reports must roll back.

- [ ] **Step 5: Write failing CLI tests**

Invoke `main(argument_vector)` and verify exit codes and output for every subcommand. `begin` must print only the absolute staging path to stdout; diagnostic messages use stderr. `fail --keep` must print the retained absolute staging path.

```python
def test_cli_begin_prints_only_staging_path(tmp_path: Path, capsys) -> None:
    assert main(["begin", "--data-root", str(tmp_path)]) == 0
    staging = Path(capsys.readouterr().out.strip())
    assert staging.parent == tmp_path.resolve() / "staging"
    assert staging.is_dir()


def test_cli_fail_keep_prints_retained_path(tmp_path: Path, capsys) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    assert main(["fail", "--staging", str(batch.staging_root), "--keep"]) == 0
    assert Path(capsys.readouterr().out.strip()) == batch.staging_root.resolve()


def test_cli_recover_without_journal_is_success(tmp_path: Path, capsys) -> None:
    assert main(["recover", "--data-root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "no_journal"


def test_cli_prepare_records_and_checkpoints_batch(tmp_path: Path) -> None:
    batch = begin_build(tmp_path, build_id="build-1")
    create_full_node_schema(batch.database)
    source_config = tmp_path / "source_roots.toml"
    source_config.write_text("[workspace]\n", encoding="utf-8")

    assert main([
        "prepare", "--staging", str(batch.staging_root),
        "--source-config", str(source_config),
        "--started-at", "2026-07-16T15:00:00Z",
        "--verified-at", "2026-07-16T15:01:00Z",
    ]) == 0

    assert read_graph_build_id(batch.database) == "build-1"
    assert json.loads((batch.workspace / "build-manifest.json").read_text())["build_id"] == "build-1"


def test_cli_publish_commits_ready_batch(tmp_path: Path) -> None:
    batch = ready_batch(tmp_path, build_id="build-1")
    assert main(["publish", "--staging", str(batch.staging_root)]) == 0
    assert read_graph_build_id(tmp_path / "android_context.db") == "build-1"
```

- [ ] **Step 6: Run CLI tests and verify RED**

```bash
python -m pytest -q tests/unit/test_build_publish.py -k cli
```

Expected: failures because CLI dispatch is absent.

- [ ] **Step 7: Implement the CLI**

Implement argparse subparsers with explicit required paths. Do not use `eval` or shell-formatted output. `prepare` accepts ISO timestamps and source-config path, records the graph node, writes the manifest, checkpoints WAL, and converts journal mode only after graph validation.

- [ ] **Step 8: Run publisher tests twice to prove idempotence**

```bash
python -m pytest -q tests/unit/test_build_publish.py
python -m pytest -q tests/unit/test_build_publish.py
```

Expected: both runs pass.

- [ ] **Step 9: Commit journaled publication and CLI**

```bash
git add android-context-current/workspace/build_publish.py android-context-current/tests/unit/test_build_publish.py
git commit -m "feat: publish graph builds with recovery journal"
```

---

### Task 4: Convert the canonical rebuild to staged paths

**Files:**
- Replace: `android-context-current/scripts/rebuild_all.sh`
- Create: `android-context-current/tests/integration/test_atomic_rebuild.py`

**Interfaces:**
- Consumes: `python -m workspace.build_publish` CLI.
- Produces command: `./scripts/rebuild_all.sh --keep-failed-db`.
- Preserves commands: `--source-config`, `--discover-only`, `--plan-only`, `--strict`, `--strict-capability`.

- [ ] **Step 1: Write failing script integration tests**

Build a temporary project with a minimal schema and stub pipeline commands. Test:

- forced importer failure preserves SHA-256 of the live database and old reports;
- default failure removes the staging batch;
- `--keep-failed-db` prints and retains the failed staging path;
- plan-only mode creates no `data/staging` directory;
- a held `data/.rebuild.lock` makes a second rebuild exit nonzero;
- a held `data/.rebuild.lock` also rejects plan-only mode before it changes workspace reports;
- successful publication exposes matching build IDs in SQL and `data/workspace/build-manifest.json`.

Mark these tests to skip outside an environment that supplies both `bash` and `flock`; the final WSL gate must run them without skips:

```python
pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("flock") is None,
    reason="atomic rebuild integration requires bash and flock",
)
```

- [ ] **Step 2: Run integration tests and verify RED**

```bash
python -m pytest -q tests/integration/test_atomic_rebuild.py
```

Expected: failures because the canonical script still deletes the live database before parsing.

- [ ] **Step 3: Implement staged orchestration**

The full rebuild branch must follow this shell structure:

```bash
exec 9>"$PROJECT_ROOT/data/.rebuild.lock"
flock -n 9 || die "another rebuild is already running"

python -m workspace.build_publish recover --data-root "$PROJECT_ROOT/data"
STAGING="$(python -m workspace.build_publish begin --data-root "$PROJECT_ROOT/data")"
STAGED_DB="$STAGING/android_context.db"
STAGED_WORKSPACE="$STAGING/workspace"
STAGED_RAW="$STAGING/raw"
```

Install an `EXIT`, `INT`, and `TERM` cleanup handler after staging is created. The handler calls `fail --staging "$STAGING"` and adds `--keep` when the user supplied `--keep-failed-db`. Disable the handler only after `publish` succeeds.

Every current pipeline path must use staged variables. Validation queries must read `STAGED_DB`. Record the graph build node and manifest, prepare the database, then call `publish`.

Before publication, preserve the current AMS and PMS queries and require at least one `EXPOSED_AS_LOCAL_SERVICE` edge:

```bash
LOCAL_SERVICE_COUNT="$(sqlite3 "$STAGED_DB" \
  "SELECT COUNT(*) FROM edge WHERE edge_type='EXPOSED_AS_LOCAL_SERVICE';")"
[[ "$LOCAL_SERVICE_COUNT" -ge 1 ]] || die "LocalServices validation failed"
```

- [ ] **Step 4: Preserve non-database modes**

Parse all options before acquiring the common lock. After locking, recover an interrupted publication. `--discover-only` and `--plan-only` then call `workspace.cli` with the published `data/workspace` output and exit before `begin`. `--help` exits before locking.

- [ ] **Step 5: Run syntax and integration tests**

```bash
bash -n scripts/rebuild_all.sh
python -m pytest -q tests/integration/test_atomic_rebuild.py
python -m pytest -q
```

Expected: syntax passes and the complete snapshot suite passes.

- [ ] **Step 6: Commit canonical staged rebuild**

```bash
git add android-context-current/scripts/rebuild_all.sh android-context-current/tests/integration/test_atomic_rebuild.py
git commit -m "feat: rebuild graph artifacts in staging"
```

---

### Task 5: Keep the self-contained installer payload synchronized

**Files:**
- Create: `scripts/verify_installer_payload.py`
- Create: `tests/test_installer_payload_sync.py`
- Modify: `install_multi_repository_source_configuration_v01.sh`
- Modify: `setup_android_context_intelligence_v1.sh`

**Interfaces:**
- Produces: `extract_payload(installer: Path, target: str) -> str`.
- Verifies payload targets:
  - `workspace/build_publish.py`;
  - `tests/unit/test_build_publish.py`;
  - `tests/integration/test_atomic_rebuild.py`;
  - `scripts/rebuild_all.sh`.

- [ ] **Step 1: Write a failing payload synchronization test**

The test loads each development snapshot file and compares it byte-for-byte with the matching quoted heredoc in `install_multi_repository_source_configuration_v01.sh`.

```python
@pytest.mark.parametrize("target,snapshot", PAYLOADS)
def test_installer_payload_matches_snapshot(target: str, snapshot: Path) -> None:
    assert extract_payload(INSTALLER, target) == snapshot.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the synchronization test and verify RED**

```powershell
python -m pytest -q tests/test_installer_payload_sync.py
```

Expected: failure because the installer does not contain the new publisher payload.

- [ ] **Step 3: Implement the payload extractor**

Parse only quoted heredocs shaped as:

```bash
cat > path/to/file <<'PY'
print("payload")
PY
```

Reject missing targets, duplicate targets, unquoted delimiters, and unterminated payloads.

- [ ] **Step 4: Embed the verified payloads**

Add the publisher module and test heredocs to the installer and replace its canonical rebuild heredoc with the tested snapshot. Add `flock` to preflight checks. Update the installer test invocation so publisher and atomic integration tests run before the full AOSP rebuild.

Update the project `.gitignore` emitted by `setup_android_context_intelligence_v1.sh` with:

```text
data/staging/
data/rollback/
data/.publish-journal.json
data/.rebuild.lock
```

- [ ] **Step 5: Run payload, Python, and shell validation**

```powershell
python -m pytest -q tests/test_installer_payload_sync.py
bash -n install_multi_repository_source_configuration_v01.sh
bash -n setup_android_context_intelligence_v1.sh
git diff --check
```

Expected: all checks pass.

- [ ] **Step 6: Commit synchronized installer payloads**

```bash
git add scripts/verify_installer_payload.py tests/test_installer_payload_sync.py \
  install_multi_repository_source_configuration_v01.sh \
  setup_android_context_intelligence_v1.sh
git commit -m "feat: install atomic graph rebuild support"
```

---

### Task 6: Document atomic rebuild operations

**Files:**
- Modify: `README.md`
- Modify: `android-context-current/README.md`
- Modify: `android-context-current/INSTALLATION_MANIFEST.txt`
- Modify: `install_multi_repository_source_configuration_v01.sh`

**Interfaces:**
- Documents: normal rebuild, retained failed build, recovery, concurrency error, and build-ID verification query.

- [ ] **Step 1: Update user documentation**

Document these commands:

```bash
./scripts/rebuild_all.sh
./scripts/rebuild_all.sh --keep-failed-db
sqlite3 data/android_context.db \
  "SELECT qualified_name FROM node WHERE node_type='GRAPH_BUILD';"
jq -r '.build_id' data/workspace/build-manifest.json
```

Explain that a retained failure is stored under `data/staging/<build-id>` and that normal cleanup never removes the last verified database.

- [ ] **Step 2: Update generated installation metadata**

Ensure the installer records `workspace/build_publish.py`, its tests, and atomic rebuild behavior in `INSTALLATION_MANIFEST.txt` without requiring a sixth install script.

- [ ] **Step 3: Validate documentation and payload consistency**

```powershell
rg -n "keep-failed-db|build-manifest|GRAPH_BUILD|staging" README.md android-context-current/README.md
python -m pytest -q tests/test_installer_payload_sync.py
git diff --check
```

Expected: documentation contains all operational concepts and checks pass.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md android-context-current/README.md \
  android-context-current/INSTALLATION_MANIFEST.txt \
  install_multi_repository_source_configuration_v01.sh
git commit -m "docs: explain atomic graph rebuild operations"
```

---

### Task 7: Verify clean upgrade and failure safety in WSL

**Files:**
- Modify after evidence: `doc/plans/2026-07-16-atomic-database-rebuild-v01-plan.md`

**Interfaces:**
- Uses the final self-contained `install_multi_repository_source_configuration_v01.sh`.
- Produces final acceptance evidence.

- [ ] **Step 1: Upgrade the existing WSL project**

```bash
cp -f /mnt/d/AndroidContextIntelligence/install_multi_repository_source_configuration_v01.sh /home/ts/
chmod +x /home/ts/install_multi_repository_source_configuration_v01.sh
/home/ts/install_multi_repository_source_configuration_v01.sh
```

Expected: unit, integration, payload, full graph, foreign-key, AMS, PMS, and LocalServices validation passes.

- [ ] **Step 2: Record the verified live database checksum**

```bash
cd /home/ts/android-context-intelligence
sha256sum data/android_context.db > /tmp/graph-db-before.sha256
```

- [ ] **Step 3: Force a staged importer failure**

Use the integration fixture rather than corrupting AOSP or production code:

```bash
source .venv/bin/activate
python -m pytest -q tests/integration/test_atomic_rebuild.py \
  -k "forced_importer_failure or keep_failed or concurrent"
```

Expected: all failure-safety tests pass.

- [ ] **Step 4: Run a real successful rebuild**

```bash
./scripts/rebuild_all.sh
sha256sum -c /tmp/graph-db-before.sha256 || true
```

The checksum may change after a successful rebuild. The command is informational; acceptance depends on matching published build IDs and graph validation.

- [ ] **Step 5: Verify batch identity and integrity**

```bash
DB_BUILD_ID="$(sqlite3 data/android_context.db \
  "SELECT qualified_name FROM node WHERE node_type='GRAPH_BUILD' LIMIT 1;")"
REPORT_BUILD_ID="$(python -c \
  "import json; print(json.load(open('data/workspace/build-manifest.json'))['build_id'])")"
test "$DB_BUILD_ID" = "$REPORT_BUILD_ID"
test -z "$(sqlite3 data/android_context.db 'PRAGMA foreign_key_check;')"
test ! -e data/android_context.db-wal
test ! -e data/android_context.db-shm
```

Expected: all commands exit zero.

- [ ] **Step 6: Run the complete suite and placeholder scan**

```bash
python -m pytest -q
grep -RInE 'TBD|TODO|implement later' \
  workspace scripts tests README.md || true
```

Expected: all tests pass and the placeholder scan has no implementation gaps.

- [ ] **Step 7: Mark the plan complete and commit evidence**

Update the status and checked steps in this plan using the actual command output, then commit:

```bash
git add doc/plans/2026-07-16-atomic-database-rebuild-v01-plan.md
git commit -m "docs: complete atomic database rebuild v0.1"
```

## Completion Gate

Do not proceed to the clean-install single-rebuild optimization until Task 7 has supplied fresh WSL evidence for every acceptance criterion in the approved design.
