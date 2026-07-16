# Multi-Repository Source Configuration v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `frameworks/base`-only pipeline with a TOML-driven multi-repository workspace that discovers repo projects, inventories languages, validates parser capabilities, and runs the existing Java Symbol, AIDL/Binder, Java Inheritance, and Java Service Registration graph layers across all supported repositories.

**Architecture:** A new `workspace` package reads `.repo/manifest.xml`, merges `config/source_roots.toml`, inventories repository languages, loads `config/parser_registry.toml`, and writes deterministic repository, language, capability, and execution-plan JSON. Existing importers consume this plan instead of hard-coded `frameworks/base` paths.

**Tech Stack:** Python 3.11+ standard library, Bash, Universal Ctags JSON, SQLite, pytest.

## Implementation Status (2026-07-16)

**Status:** Complete. Core implementation, current-workspace acceptance, and the complete Task 12 audit passed.

Verified in WSL on `/home/ts/android-context-intelligence`:

- Workspace unit tests: `6 passed`.
- Two-repository graph integration test: `1 passed`.
- Entire installed-project test suite: `24 passed`.
- Non-strict planning continued with structured Kotlin coverage gaps.
- Strict planning failed on the unsupported Kotlin fixture as expected.
- Repo workspace discovery found 1087 projects and rebuilt the enabled repository set.
- Java Symbol, AIDL/Binder, Java Inheritance, and Service Registration layers completed.
- `PRAGMA foreign_key_check` passed.
- The `activity -> ActivityManagerService -> IActivityManager` chain passed.
- The transitive `package -> IPackageManagerImpl -> IPackageManagerBase -> IPackageManager` chain passed.

The capability report audit found 18 valid entries with no missing fields. Unsupported entries were visible for every relevant language actually detected in the enabled repository set. Rust was not present because no Rust file was detected in the currently enabled `frameworks/base` scan; v0.1 must not manufacture a zero-file coverage entry.

The final installed-project placeholder scan and direct SQLite foreign-key query both produced no output, completing the Task 12 audit.

Post-v0.1 hardening is tracked separately and is not a blocker for the functional acceptance above: staged/atomic database replacement, a single final rebuild during clean installation, and lossless provenance for duplicate qualified names.

## Global Constraints

- Default AOSP root: `/home/ts/aosp`.
- Default project root: `/home/ts/android-context-intelligence`.
- TOML only, parsed with `tomllib`; no PyYAML.
- Unsupported languages are reported and skipped by default.
- `--strict` and `--strict-capability` turn relevant coverage gaps into failures.
- Detect Java, AIDL, Kotlin, C, C++, Rust, HIDL, Python, Blueprint, Make, and Proto.
- v0.1 semantic importers remain Java/AIDL only.
- Existing AMS, PMS, LocalServices, inheritance, Binder, and foreign-key validations must keep passing.
- Final delivery is one reusable `/home/ts/install_multi_repository_source_configuration_v01.sh`.
- Future clean setup must not require patch scripts.

---

### Task 1: Add workspace models and TOML configuration

**Files**
- Create `workspace/models.py`
- Create `workspace/config.py`
- Create `config/source_roots.toml`
- Test `tests/unit/test_workspace_config.py`

**Interfaces**
- `load_workspace_config(path: Path) -> WorkspaceConfig`
- `RepositorySpec`
- `RepositoryOverride`
- `WorkspacePlan.to_dict() -> dict[str, object]`

- [ ] Write failing tests for slash-containing repository names, include/exclude lists, language whitelists, extra repositories, unknown languages, and deterministic serialization.
- [ ] Run `pytest -q tests/unit/test_workspace_config.py`; expect import failures.
- [ ] Implement frozen dataclasses and TOML validation with `tomllib`.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: add workspace configuration models"`.

### Task 2: Discover repositories from repo manifests

**Files**
- Create `workspace/manifest.py`
- Test `tests/unit/test_workspace_manifest.py`

**Interfaces**
- `parse_repo_manifest(manifest_path: Path) -> tuple[RepositorySpec, ...]`
- `ManifestError`

- [ ] Write tests for `<project>`, omitted `path`, nested `<include>`, duplicate paths, missing includes, and include cycles.
- [ ] Run tests; expect failure.
- [ ] Implement recursive XML parsing with active-stack cycle detection.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: discover repo manifest projects"`.

### Task 3: Detect repository languages

**Files**
- Create `workspace/languages.py`
- Test `tests/unit/test_workspace_languages.py`

**Interfaces**
- `detect_languages(repository, aosp_root, default_excludes) -> LanguageInventory`

**Detection**
- Java `.java`
- AIDL `.aidl`
- Kotlin `.kt`, `.kts`
- C `.c`, `.h`
- C++ `.cc`, `.cpp`, `.cxx`, `.hpp`, `.hh`
- Rust `.rs`
- HIDL `.hal`
- Python `.py`
- Blueprint `Android.bp`
- Make `Android.mk`, `.mk`
- Proto `.proto`

- [ ] Write fixture tests covering include/exclude pruning and language counts.
- [ ] Run tests; expect failure.
- [ ] Implement directory-pruned scanning; do not traverse `.git`, `.repo`, `out`, tests, benchmarks, prebuilts, node_modules, or caches unless configuration explicitly includes them.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: inventory repository languages"`.

### Task 4: Add parser capability registry

**Files**
- Create `config/parser_registry.toml`
- Create `workspace/registry.py`
- Test `tests/unit/test_workspace_registry.py`

**Interfaces**
- `load_parser_registry(path: Path) -> dict[str, ParserSpec]`
- `parser_for(language, capability) -> ParserSpec | None`

**Built-in matrix**
- Java: symbols, inheritance, service_registration, permission_enforcement
- AIDL: symbols, binder
- Kotlin/C/C++/Rust/HIDL: detected but unsupported in v0.1

- [ ] Write tests for supported, disabled, missing implementation, unknown language, and unknown capability.
- [ ] Run tests; expect failure.
- [ ] Implement validated registry loading.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: add parser capability registry"`.

### Task 5: Build repository inventory and execution plan

**Files**
- Create `workspace/planner.py`
- Create `workspace/cli.py`
- Test `tests/unit/test_workspace_planner.py`

**Interfaces**
- `build_workspace_plan(config_path, registry_path, strict=False, strict_capability=None) -> WorkspacePlan`
- `CoverageError`
- CLI writes:
  - `data/workspace/repositories.json`
  - `data/workspace/language-inventory.json`
  - `data/workspace/capability-report.json`
  - `data/workspace/execution-plan.json`

- [ ] Write tests for manifest/config merge, disabled repositories, extra repositories, missing paths, language whitelist, unsupported tasks, deterministic sorting, strict failures, and capability-scoped strictness.
- [ ] Run tests; expect failure.
- [ ] Implement planning and atomic JSON writes.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: generate workspace execution plans"`.

### Task 6: Convert Java Symbol Graph to multi-repository input

**Files**
- Modify current Java Ctags/importer module
- Modify `scripts/rebuild_all.sh`
- Test `tests/integration/test_multi_repository_pipeline.py`

**Interface**
```bash
python -m collectors.source.<java_importer>   --workspace-plan data/workspace/execution-plan.json   --aosp-root /home/ts/aosp   --db data/android_context.db   --raw-dir data/raw/ctags
```

- [ ] Write a two-repository Java test and verify the second repository is absent before implementation.
- [ ] Generate one Ctags JSONL per scheduled Java repository with `--fields=+nKSEi`.
- [ ] Add repository metadata to imported nodes while preserving globally unique qualified-name IDs.
- [ ] Report duplicate qualified names instead of silently overwriting provenance.
- [ ] Run Java unit and integration tests; expect PASS.
- [ ] Commit with `git commit -m "feat: import Java symbols from multiple repositories"`.

### Task 7: Convert AIDL/Binder Graph to multi-repository input

**Files**
- Modify current AIDL/Binder importer
- Expand `tests/integration/test_multi_repository_pipeline.py`

- [ ] Write a fixture with AIDL in `frameworks/base` and another repository.
- [ ] Verify the second interface is absent before implementation.
- [ ] Scan every scheduled AIDL repository and build one global package/interface index before resolving Binder implementations.
- [ ] Include repository IDs in reports and unresolved relations.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: import AIDL across repositories"`.

### Task 8: Convert Java Inheritance Graph to all Java Ctags outputs

**Files**
- Modify `collectors/source/java_inheritance_importer.py`
- Expand integration tests

**Interface**
```bash
--workspace-plan data/workspace/execution-plan.json
--ctags-dir data/raw/ctags
```

- [ ] Write a cross-repository inheritance test.
- [ ] Verify the edge is absent before implementation.
- [ ] Load all Java DB types once and iterate every scheduled repository Ctags file.
- [ ] Preserve child-to-parent edge direction and repository metadata in unresolved records.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: resolve inheritance across repositories"`.

### Task 9: Convert Service Registration Graph to scheduled Java repositories

**Files**
- Modify `collectors/service/service_registration_importer.py`
- Expand integration tests

**Interface**
```bash
--workspace-plan data/workspace/execution-plan.json
--aosp-root /home/ts/aosp
```

- [ ] Write a vendor Java service registration fixture.
- [ ] Verify `REGISTERED_AS` is absent before implementation.
- [ ] Scan all scheduled Java repositories.
- [ ] Include repository ID plus repository-relative path in `SERVICE_REGISTRATION` stable identities.
- [ ] Leave unsupported Kotlin/C++/Rust registration tasks in capability reports; do not claim coverage.
- [ ] Run tests; expect PASS.
- [ ] Commit with `git commit -m "feat: scan service registrations across repositories"`.

### Task 10: Rebuild canonical orchestration

**Files**
- Modify `scripts/rebuild_all.sh`
- Create `queries/workspace_coverage_summary.sql`
- Expand integration tests

**Supported commands**
```bash
./scripts/rebuild_all.sh
./scripts/rebuild_all.sh --source-config config/source_roots.toml
./scripts/rebuild_all.sh --discover-only
./scripts/rebuild_all.sh --plan-only
./scripts/rebuild_all.sh --strict
./scripts/rebuild_all.sh --strict-capability permission_enforcement
```

- [ ] Write subprocess tests for argument handling and non-destructive plan-only modes.
- [ ] Parse arguments before resetting the DB.
- [ ] Execute in order: workspace plan, DB reset, Java, AIDL/Binder, inheritance, service registration, FK validation, AMS/PMS/LocalServices validation, coverage summary.
- [ ] Ensure `--discover-only` and `--plan-only` do not change the database.
- [ ] Run `bash -n scripts/rebuild_all.sh` and integration tests.
- [ ] Commit with `git commit -m "feat: orchestrate multi-repository rebuilds"`.

### Task 11: Build reusable installer and update clean bootstrap

**Files**
- Create `/home/ts/install_multi_repository_source_configuration_v01.sh`
- Modify `/home/ts/setup_android_context_intelligence_v1.sh`
- Modify `README.md`
- Modify `INSTALLATION_MANIFEST.txt`

- [ ] Embed every generated file in quoted heredocs; do not depend on `/tmp` fragments.
- [ ] Preflight `python3`, `ctags`, `sqlite3`, `git`, AOSP root, project root.
- [ ] Back up modified files.
- [ ] Run all unit/integration tests.
- [ ] Run non-strict unsupported-language fixture and expect exit 0 with structured report.
- [ ] Run strict fixture and expect nonzero exit.
- [ ] Run capability-scoped strict fixture.
- [ ] Run full rebuild and verify `foreign_key_check: PASS`, AMS, PMS, and LocalServices.
- [ ] Update clean bootstrap so no patch script is required.
- [ ] Run `bash -n` on installer, bootstrap, and rebuild script.
- [ ] Commit with `git commit -m "feat: install multi-repository source configuration"`.

### Task 12: Final verification

- [x] Run `pytest -q`; all 24 tests passed.
- [x] Run `./scripts/rebuild_all.sh`; all graph validations passed.
- [x] Inspect `data/workspace/capability-report.json`; all 18 entries contain repository, language, capability, parser, status, and file count.
- [x] Verify unsupported Kotlin/C/C++/Rust entries are visible when the corresponding language is detected. Kotlin, C, and C++ were detected and reported; Rust was not detected in the enabled repository set.
- [x] Verify strict planning fails on an unsupported-language fixture.
- [x] Verify the canonical rebuild reports `foreign_key_check: PASS`.
- [x] Search for unresolved placeholders; the scan produced no output:
```bash
grep -RInE 'TBD|TODO|implement later'   workspace config scripts collectors tests README.md
```
Expected: no implementation placeholders.
