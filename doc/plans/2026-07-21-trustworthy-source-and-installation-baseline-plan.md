# Trustworthy Source and Installation Baseline Implementation Plan

> **Required sub-skill:** Use `superpowers:executing-plans` to execute this plan task by task, and `superpowers:test-driven-development` for every behavior change.

**Goal:** Make the tracked Windows Git repository the single readable source of truth, install the project into WSL deterministically, and remove the generated WSL tree from all source/build inputs.

**Architecture:** Track the generated-project implementation in `project/`. A small Python payload library owns file selection, hashing, comparison, and installation; shell entry points only validate their own arguments and delegate. Fresh installs stage and atomically promote a complete source tree. Upgrades preserve explicitly declared runtime/local paths and retain a rollback copy. The deployed WSL project is disposable and verifiable through an installed manifest.

**Technology:** Python 3.11+, Bash, pytest, SHA-256 JSON manifests, Git worktrees, WSL2.

## Global constraints

- Work only on `codex/trustworthy-source-baseline` in the isolated worktree.
- Add a failing test before every production-code behavior change.
- Commit each task independently; never mix unrelated user changes.
- Do not read the untracked Windows `android-context-current/` snapshot as a normal build input.
- Do not modify `~/android-context-intelligence`, its `.venv`, or its `data/` during implementation. WSL acceptance uses a temporary target.
- `project/` is the canonical implementation. Installers may not contain independent Python, SQL, TOML, test, or `rebuild_all.sh` copies.
- Preserve existing graph behavior in this milestone. Permission, Vendor, and provenance corrections remain follow-on work.

## Task 1: Define the canonical payload boundary and repair repository hygiene

**Files:**

- Replace: `.gitignore`
- Create: `pyproject.toml`
- Create: `scripts/project_payload.py`
- Create: `tests/test_project_payload.py`

### TDD sequence

1. Add tests proving that payload selection:
   - includes declared top-level files and source directories;
   - returns stable POSIX-relative paths in sorted order;
   - excludes `.git`, `.venv`, `data`, caches, bytecode, databases, backups, and raw reports;
   - reports added, removed, and modified files without comparing runtime-only paths.
2. Run `python -m pytest -q tests/test_project_payload.py` in WSL and verify failure because `scripts.project_payload` does not exist.
3. Implement:

   ```python
   PAYLOAD_DIRECTORIES: tuple[str, ...]
   PAYLOAD_FILES: tuple[str, ...]
   iter_payload_files(root: Path) -> tuple[Path, ...]
   payload_hashes(root: Path) -> dict[str, str]
   compare_payload(expected: Path, actual: Path) -> PayloadDiff
   ```

4. Re-run the focused test, then the root test suite.
5. Rewrite `.gitignore` as UTF-8 text and explicitly ignore `.worktrees/`, `android-context-current/`, runtime databases, caches, and temporary install directories.
6. Add pytest configuration that limits root discovery to `tests/`.
7. Run `git diff --check` and commit:

   ```text
   chore: define canonical project payload boundary
   ```

## Task 2: Materialize the readable canonical `project/` tree

**Files:**

- Create: `project/.gitignore`
- Create: `project/INSTALLATION_MANIFEST.txt`
- Create: `project/README.md`
- Create: `project/requirements-lock.txt`
- Create: `project/collectors/**`
- Create: `project/config/**`
- Create: `project/configs/**`
- Create: `project/graph/**`
- Create: `project/queries/**`
- Create: `project/scripts/**`
- Create: `project/storage/**`
- Create: `project/tests/**`
- Create: `project/workspace/**`
- Create: `tests/test_canonical_project.py`
- Create: `doc/reviews/2026-07-21-source-drift-audit.md`

### TDD and migration sequence

1. Add a test requiring every payload allowlist entry to exist under `project/`, no forbidden runtime path to be tracked, Python package entry points to import, and canonical shell scripts to be syntax-valid.
2. Verify the test fails because `project/` is absent.
3. Copy only the approved source allowlist from the current deployed WSL project. Exclude `.git`, `.venv`, `backups`, `data`, `.pytest_cache`, `__pycache__`, `*.pyc`, and `*.bak*`.
4. Compare each copied file with the corresponding effective installer payload where it exists. Record every mismatch and the selected source in the drift audit; do not silently choose based on timestamp.
5. Compile canonical Python source and run canonical project pytest.
6. Run the root suite and `git diff --check`.
7. Commit:

   ```text
   chore: track canonical project source
   ```

## Task 3: Add payload manifests and drift verification

**Files:**

- Modify: `scripts/project_payload.py`
- Create: `scripts/verify_project_install.py`
- Create: `tests/test_payload_manifest.py`
- Create: `tests/test_verify_project_install.py`

### TDD sequence

1. Add tests for a stable, sorted JSON manifest containing schema version, source commit, and SHA-256 per payload file.
2. Add CLI tests for clean, modified, missing, and unexpected managed files. The CLI must return nonzero for drift and print all categories.
3. Verify both focused test modules fail for missing APIs.
4. Implement:

   ```python
   write_manifest(payload_root: Path, output: Path, source_commit: str) -> None
   load_manifest(path: Path) -> PayloadManifest
   verify_manifest(target: Path, manifest: PayloadManifest) -> PayloadDiff
   ```

   Manifest writes use a sibling temporary file, flush, `fsync`, and `os.replace`.
5. Implement `verify_project_install.py --target PATH [--manifest PATH]`.
6. Run focused tests, root tests, and `git diff --check`.
7. Commit:

   ```text
   feat: add project payload manifests and drift checks
   ```

## Task 4: Implement deterministic fresh and upgrade installation

**Files:**

- Create: `scripts/install_project.py`
- Create: `installers/install_project.sh`
- Create: `tests/test_install_project.py`
- Create: `tests/test_install_project_shell.py`

### Required contract

```text
install_project.py --fresh --source PROJECT --target TARGET
install_project.py --upgrade --source PROJECT --target TARGET
install_project.py --verify-only --source PROJECT --target TARGET
```

Exactly one mode is required. Managed-source paths come only from the payload library. Preserved paths are `data/`, `.venv/`, `config/source_roots.toml`, and `configs/local.yaml` when present.

### TDD sequence

1. Add tests proving fresh install stages, verifies, and promotes a byte-identical payload without touching an existing target when validation fails.
2. Add upgrade tests proving runtime/local paths survive, obsolete previously managed files are removed, a rollback directory is retained, and failure restores the old target.
3. Add tests for unknown options, conflicting modes, custom target paths, and `--verify-only` drift exit codes.
4. Verify tests fail because the installer does not exist.
5. Implement the minimal Python installer. Use adjacent staging and rollback paths, `shutil.copy2`, generated manifest verification, and `os.replace`; never merge directly into the live target.
6. Implement the Bash adapter as a thin `exec python3 .../scripts/install_project.py` wrapper with no payload heredocs and no prompt.
7. Run focused tests, root tests, shell syntax checks, and `git diff --check`.
8. Commit:

   ```text
   feat: install canonical project payload deterministically
   ```

## Task 5: Replace self-extracting stages with one non-interactive setup path

**Files:**

- Rewrite: `setup.sh`
- Rewrite: `installers/setup_android_context_intelligence_v1.sh`
- Rewrite: `installers/install_java_inheritance_graph_v01.sh`
- Rewrite: `installers/install_system_service_registration_graph_v01.sh`
- Rewrite: `installers/install_multi_repository_source_configuration_v01.sh`
- Rewrite: `installers/install_permission_enforcement_graph_v01.sh`
- Rewrite: `installers/install_vendor_graph_v01.sh`
- Create: `tests/test_setup_contract.py`
- Replace: `tests/test_installer_payload_sync.py`

### Required setup contract

```text
./setup.sh --fresh [--rebuild]
./setup.sh --upgrade [--rebuild]
./setup.sh --verify-only
```

`AOSP_ROOT` and `PROJECT_ROOT` environment variables are honored. AOSP validation is required only when `--rebuild` is requested. No path prompts or Vendor prompt are allowed.

### TDD sequence

1. Replace obsolete snapshot tests with contract tests that require every compatibility stage to delegate to the canonical setup/installer.
2. Add static tests rejecting `cat >`, `base64 -d`, embedded `rebuild_all.sh`, and `read -p` in shipped installers.
3. Add subprocess tests for custom roots, verify-only operation, unknown arguments, and no-stdin execution.
4. Verify failures against the current self-extracting installers.
5. Rewrite `setup.sh` as an argument parser and delegator. Invoke canonical rebuild only when explicitly requested and only after successful installation.
6. Replace legacy stages with compatibility wrappers that print a deprecation message and delegate. They must not independently write project files.
7. Run root tests and syntax-check all shell scripts.
8. Commit:

   ```text
   refactor: replace staged payload installers with canonical setup
   ```

## Task 6: Repair canonical project integration contracts

**Files:**

- Modify: `project/tests/integration/test_atomic_rebuild.py`
- Modify: `project/INSTALLATION_MANIFEST.txt`
- Modify only as required by failing canonical tests: `project/tests/**`

### TDD sequence

1. Reproduce the existing atomic rebuild failure in the canonical tree.
2. Add the missing `workspace/multi_permission.py` report fixture before importing the real pipeline.
3. Re-run the focused integration test and verify it passes without parsing CLI arguments at import time.
4. Run all canonical project tests and all root tests.
5. Update the installation manifest to describe the canonical source and installed-manifest contract.
6. Commit:

   ```text
   test: restore release and atomic rebuild contracts
   ```

## Task 7: Publish the architecture/risk review and operating documentation

**Files:**

- Create: `doc/reviews/2026-07-21-repository-architecture-review.md`
- Modify: `README.md`
- Modify: `project/README.md`
- Modify: `doc/README.md`

### Documentation requirements

1. Rank the confirmed findings by severity and cite repository/WSL evidence.
2. State that `project/` is authoritative and `~/android-context-intelligence` is a disposable deployment.
3. Explain what must be copied for a clean AOSP machine: the Git checkout or release package containing `setup.sh`, `installers/`, `scripts/`, and `project/`; the generated WSL directory is never copied.
4. Document all setup, verify, upgrade, rebuild, and drift-check commands.
5. Correct Permission and Vendor completion claims to match verified live database evidence.
6. Record follow-on priorities: Permission scheduling/semantics, Vendor atomic ingestion, repository provenance, then Build/incremental/runtime graphs.
7. Run link/path searches and `git diff --check`.
8. Commit:

   ```text
   docs: align operations and roadmap with verified state
   ```

## Task 8: WSL acceptance and completion audit

**Files:**

- Create: `doc/reviews/2026-07-21-trustworthy-baseline-acceptance.md`
- Modify only if acceptance exposes a tested defect.

### Acceptance sequence

1. Record SHA-256 and build ID for the live WSL database before testing.
2. Install from the Windows worktree into `/home/ts/aci-trustworthy-baseline-test` using `--fresh`, with stdin closed.
3. Run `--verify-only`, compare installed managed files with `project/`, compile Python, run installed project pytest, and syntax-check installed shell scripts.
4. Exercise `--upgrade` after creating sentinel files under every preserved runtime/local path; prove all sentinels survive and managed drift is corrected.
5. Re-check the live WSL database hash/build ID and prove the real project was untouched.
6. Run final verification:

   ```bash
   python -m pytest -q
   python -m pytest -q project/tests
   python -m compileall -q project scripts
   bash -n setup.sh installers/*.sh project/scripts/*.sh
   git diff --check
   git status --short
   ```

7. Record commands and exact results in the acceptance document.
8. Commit:

   ```text
   test: verify trustworthy baseline installation in WSL
   ```

## Completion gate

Do not declare this milestone complete unless current evidence proves all of the following:

- `project/` is tracked and no installer owns a second source copy.
- Root and canonical project tests pass.
- All shipped shell scripts pass `bash -n`.
- A temporary fresh install and upgrade reproduce the managed tree and preserve declared runtime/local paths.
- Drift verification detects additions, removals, and changes.
- Custom `PROJECT_ROOT` works in every mode.
- Setup never requires interactive input.
- The existing live WSL project and database were not modified by acceptance.
- Documentation accurately distinguishes verified current state from planned Permission, Vendor, and provenance work.
- Every implementation batch is represented by a focused Git commit on the feature branch.

