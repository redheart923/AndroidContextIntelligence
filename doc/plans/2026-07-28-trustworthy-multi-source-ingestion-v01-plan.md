# Trustworthy Multi-Source Ingestion v0.1 Implementation Plan

> Status: Proposed. Start only after Permission Semantics Graph v0.1 is
> accepted and merged.

**Goal:** Make graph coverage declarations, upgrades, cross-repository identity,
source provenance, and Vendor ingestion deterministic and transaction-safe.

**Architecture:** Keep `project/` as the canonical payload and
`scripts/rebuild_all.sh` as the only graph publication entry point. Separate
logical symbols from repository-scoped definitions, separate canonical source
defaults from local overrides, and make every parser publish a structured
coverage result. Vendor decompilation becomes a content-addressed source
preparation stage consumed by the same staged graph build.

## Task 1 — Capability quality and runtime evidence

**Files:**

- Modify: `project/workspace/models.py`
- Modify: `project/workspace/registry.py`
- Modify: `project/workspace/planner.py`
- Modify: `project/config/parser_registry.toml`
- Create: `project/workspace/coverage_validation.py`
- Test: `project/tests/unit/test_workspace_v01.py`
- Test: `project/tests/unit/test_coverage_validation.py`

Steps:

1. Write failing tests for `semantic`, `heuristic`, `tags_only`, and
   `unsupported` capability quality.
2. Extend parser specs and plan tasks with quality and expected evidence.
3. Remove the unconditional Kotlin inheritance claim or mark it heuristic.
4. Make strict gates validate parser output, not scheduling alone.
5. Emit observed counts and degradation reasons in the capability report.
6. Run unit tests and commit independently.

Acceptance:

- a scheduled parser with zero required evidence cannot report full support;
- Kotlin inheritance truthfully reports its observed coverage;
- existing Java/AIDL/Permission strict gates remain green.

## Task 2 — Canonical defaults and local configuration migration

**Files:**

- Create: `project/config/source_roots.default.toml`
- Create: `project/config/source_roots.local.toml.example`
- Modify: `project/workspace/config.py`
- Modify: `scripts/install_project.py`
- Modify: `scripts/project_payload.py`
- Test: `tests/test_install_project.py`
- Test: `project/tests/unit/test_workspace_v01.py`

Steps:

1. Write upgrade tests that preserve local repository choices while accepting
   new canonical include roots.
2. Load immutable defaults first and overlay an optional local file.
3. Migrate an existing preserved `source_roots.toml` to the local override
   format without silently losing values.
4. Record both configuration digests in the build manifest.
5. Update fresh, upgrade, verify, and rollback tests.
6. Commit independently.

Acceptance:

- fresh and upgraded deployments receive the same canonical required roots;
- local enable/disable/include/exclude choices survive upgrade;
- config migration failure rolls back the installation.

## Task 3 — Repository-scoped definitions and collision gate

**Files:**

- Modify: `project/storage/schema.sql`
- Modify: `project/graph/writer.py`
- Modify: `project/collectors/source/ctags_importer.py`
- Modify: `project/workspace/pipeline.py`
- Create: `project/workspace/symbol_collision_validation.py`
- Test: `project/tests/unit/test_graph_writer_identity.py`
- Test: `project/tests/integration/test_multi_repository_pipeline.py`

Steps:

1. Write a two-repository collision test that currently overwrites a node.
2. Introduce repository-scoped definition identity and a logical-symbol link.
3. Preserve every source definition and mark ambiguous logical resolution.
4. Fail strict publication on unresolved collisions that affect semantic edges.
5. Migrate queries to use explicit logical or definition identity.
6. Commit independently.

Acceptance:

- scan order does not discard a definition;
- ambiguity is queryable and cannot silently select a winner;
- cross-repository inheritance still resolves when unambiguous.

## Task 4 — Reproducible source and tool provenance

**Files:**

- Modify: `project/workspace/revisions.py`
- Modify: `project/workspace/models.py`
- Modify: `project/workspace/build_publish.py`
- Modify: `project/scripts/rebuild_all.sh`
- Test: `project/tests/unit/test_permission_workspace.py`
- Test: `project/tests/unit/test_build_publish.py`

Steps:

1. Write tests for clean, dirty, non-Git, and missing repositories.
2. Record commit plus dirty state and a relevant source inventory digest.
3. Record source config, parser registry, Ctags, Python, SQLite, and optional
   JADX identities.
4. Include provenance in the verified build manifest and graph build node.
5. Add a provenance validation command.
6. Commit independently.

Acceptance:

- dirty trees are distinguishable from clean trees at the same commit;
- two fingerprints can be traced to exact source/config/tool inputs;
- missing provenance can fail a requested strict gate.

## Task 5 — Vendor artifact preparation and staged graph integration

**Files:**

- Replace: `project/scripts/import_vendor.sh`
- Create: `project/workspace/vendor_artifacts.py`
- Create: `project/workspace/multi_vendor.py`
- Modify: `project/scripts/rebuild_all.sh`
- Modify: `project/workspace/build_publish.py`
- Test: `project/tests/unit/test_vendor_artifacts.py`
- Test: `project/tests/integration/test_vendor_atomic_rebuild.py`

Steps:

1. Write tests proving the current live-database mutation is forbidden.
2. Define an input directory outside `data/` and a content-addressed cache.
3. Record artifact SHA-256, size, JADX version/options, exit status, and output
   digest.
4. Treat partial decompilation as explicit degraded evidence, never silent
   success.
5. Import prepared Java sources only into the active staged database.
6. Validate and publish through the existing build transaction.
7. Keep the old command as a compatibility wrapper that cannot target live DB.
8. Commit independently.

Acceptance:

- interruption or parser failure leaves the live database unchanged;
- changing an artifact or JADX version invalidates the cache;
- a successful build can trace every Vendor fact to an artifact manifest.

## Task 6 — Service pipeline profiling and bounded optimization

**Files:**

- Create: `project/scripts/profile_service_registration.py`
- Modify: `project/workspace/multi_service.py`
- Modify: `project/collectors/service/service_registration_importer.py`
- Test: `project/tests/unit/test_service_registration_importer.py`

Steps:

1. Capture a repeatable frameworks/base profile and correctness baseline.
2. Index candidate files before constructing full source models.
3. Cache file-local parsing by content digest.
4. Retain all existing service-chain acceptance queries.
5. Report before/after elapsed time and graph fingerprint.
6. Commit independently.

Acceptance:

- AMS, PMS, and LocalServices results are unchanged;
- service graph fingerprint is unchanged;
- measured runtime improves without weakening diagnostics.

## Task 7 — Repository and real-source acceptance

1. Run root and canonical project test suites.
2. Run Python compilation, Bash syntax checks, and `git diff --check`.
3. Exercise fresh, upgrade, verify, rollback, and config migration.
4. Perform two clean real-AOSP builds and compare graph fingerprints.
5. Run a Vendor fixture build and an interrupted-publication test.
6. Write a dated acceptance record under `doc/reviews/`.
7. Request code review, address findings, and merge locally to `main`.

## Explicit non-goals

- full Kotlin AST semantics;
- C/C++ or Rust semantic parsers;
- HIDL and native Binder graphs;
- Build/Soong graph;
- runtime trace ingestion;
- AI reasoning over unvalidated facts.

Those domains should use the trustworthy ingestion contract established here.
