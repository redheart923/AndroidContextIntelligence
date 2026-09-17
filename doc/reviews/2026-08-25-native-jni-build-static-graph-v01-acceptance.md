# Native/JNI/Build Static Graph v0.1 Acceptance

Status: accepted for fixtures and the documented WSL partial-source scope.

## Accepted fixture evidence

- Native parser/JNI focused suite after the Tree-sitter compatibility correction:
  `18 passed in 2.08s`.
- Project suite after that correction: `390 passed in 58.80s`.
- Native atomic fixture performs two rebuilds and asserts equal semantic fingerprints.
- Parser failure, source/build-input changes, candidate isolation, SQLite WAL snapshot,
  foreign-key integrity and optional-input behavior are covered by committed tests.
- Known legacy/current Blueprint fixtures publish active modules; unknown future
  semantics degrade and do not create false active edges.

The final WSL fixture gate was rerun with the installed project:

```bash
cd /home/ts/android-context-intelligence
.venv/bin/python -m pytest -q \
  tests/integration/test_native_atomic_rebuild.py::test_partial_fixture_publishes_atomically_and_second_run_is_deterministic \
  tests/integration/test_native_atomic_rebuild.py::test_known_blueprint_versions_publish_modules
```

```text
...                                                                      [100%]
3 passed in 0.59s
```

## WSL partial acceptance

Environment:

- installed project: `/home/ts/android-context-intelligence`
- AOSP root: `/home/ts/aosp`
- enabled repositories: `frameworks/base`, `frameworks/libs/systemui`,
  `frameworks/native`
- Tree-sitter runtime: `tree-sitter==0.25.2`

The canonical atomic rebuild was run from the installed project:

```bash
cd /home/ts/android-context-intelligence
./scripts/rebuild_all.sh
```

Relevant literal output:

```text
Repositories discovered: 1087; enabled: 3; unsupported capability entries: 11
source_scope_validation: preflight_passed
Schema migrations: 0001_call_dataflow, 0002_effective_fact_views, 0003_native_build_candidates, 0004_native_candidate_corrections
Imported 390559 java symbols; skipped 0; owner edges 357120; unresolved owners 440
Imported 353022 kotlin symbols; skipped 0; owner edges 27023; unresolved owners 297006
AIDL interfaces: 928; methods: 6723; Binder relations: 177; unresolved: 514
Service registrations: 317; resolved: 138; candidates: 4360/14368; excluded candidates: 1123; cache hits: 0; elapsed: 617.336s
Permission semantics: 7049 edges; 0 task failures
permission_validation: PASS
Native pipeline: published; candidates: 328; fingerprint: 1589abfcdd49066d93c8000907cf934769c1b8e2780ede4969e122acd4d7be619
Graph corrections: 0 applied
call_dataflow_validation: PASS; call_sites=0; paths=0
Symbol collisions: 6; semantic: 0
Runtime coverage: degraded: 48; supported: 18; unsupported: 11
source_scope_validation: passed
source_scope_validation: fingerprint_bound
foreign_key_check: PASS
```

The published database was queried directly:

```bash
sqlite3 -header -column data/android_context.db \
  "SELECT version,name FROM schema_migration ORDER BY version;
   SELECT COUNT(*) AS raw_candidates FROM node
     WHERE node_type='EXTRACTION_CANDIDATE';
   SELECT COUNT(*) AS effective_candidates FROM effective_node
     WHERE node_type='EXTRACTION_CANDIDATE';"
```

```text
version  name
-------  ---------------------------------
1        0001_call_dataflow
2        0002_effective_fact_views
3        0003_native_build_candidates
4        0004_native_candidate_corrections

raw_candidates
--------------
1785

effective_candidates
--------------------
0
```

Effective graph summaries:

```text
CPP_FUNCTION     7888
CPP_METHOD       14322
NATIVE_FUNCTION  2628
RUST_FUNCTION    985
SOONG_MODULE     1490

DEPENDS_ON       1631
INCLUDES         12021
JNI_BINDS_TO     318
USES_DEFAULTS    549
```

This is partial-source acceptance. It proves that the configured three-repository
scope can be rebuilt atomically, its candidates remain outside effective views,
and the resulting structural Native/JNI/Soong graph passes the published
validation gates.

## Final deployment gates

The final source tree was verified before commit:

```text
repository-root pytest: 60 passed in 38.99s
project pytest:         390 passed in 56.02s
git diff --check:       PASS (line-ending notices only)
bash -n setup.sh:       PASS
bash -n project/scripts/rebuild_all.sh: PASS
```

The tested payload was then upgraded without rebuilding or replacing local
configuration:

```bash
PROJECT_ROOT=/home/ts/android-context-intelligence bash ./setup.sh --upgrade
PROJECT_ROOT=/home/ts/android-context-intelligence bash ./setup.sh --verify-only
```

```text
upgrade installation: PASS (/home/ts/android-context-intelligence)
Android Context Intelligence setup: PASS
payload verification: PASS (228 managed files)
Android Context Intelligence setup: PASS
```

## Explicit boundaries

- Full AOSP rebuild: NOT RUN. Only the three repositories listed above were
  enabled.
- Generated Soong/Ninja acceptance: NOT RUN. No generated Ninja,
  `compile_commands.json`, `rust-project.json`, or `module-info.json` inputs were
  supplied. Android.bp source declarations were parsed, but that does not prove
  generated build-action fidelity.
- Precision native call/dataflow acceptance: NOT RUN. The validation output
  explicitly reports `call_sites=0; paths=0`; no CodeQL native call/dataflow
  extraction was supplied in this run.
- Kotlin semantic completeness: **NOT CLAIMED**. Ctags-backed Kotlin symbols are
  structural and retained their unresolved-owner count.
- The non-fatal Ctags warnings for a few non-source/missing paths under
  `frameworks/native` remain scanner noise and are not counted as covered source.

Fixture and WSL partial results do not imply full AOSP coverage. Ctags and
Tree-sitter facts are structural/heuristic unless the capability report records
stronger evidence quality.
