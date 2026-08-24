# Partial Source Workspace Profile v0.1 Acceptance

Status: implementation, fixture verification, code review, and a real WSL partial-source
smoke build are complete. Full-AOSP build/graph acceptance was not run because the local
checkout is intentionally incomplete.

## Accepted scope

- Explicit `analysis_scope = "aosp" | "partial"`; omitted configuration remains `aosp`.
- Deterministic repository identity from revision state and filtered source inventory.
- Pre-import and post-import source-scope gates with stable failure reason codes.
- Partial builds require source-backed graph evidence and completed capability reports, but
  do not require AMS, PMS, LocalServices, or SystemUI representative facts.
- AOSP-profile builds retain the LocalServices representative-evidence gate.
- Scope report identity is bound to semantic fingerprints, provenance, `GRAPH_BUILD`, the
  build manifest, retained history, and atomic publication.
- Published reports remain independently verifiable after the staging directory is moved.

## Automated verification

Executed from WSL against branch `codex/partial-source-workspace-v01` on 2026-08-24:

```text
python -m compileall -q project/workspace project/collectors scripts tests: PASS
bash -n project/scripts/rebuild_all.sh: PASS
affected scope/provenance/publication suites: 67 passed in 20.08s
project suite: 266 passed in 33.27s
root suite: 58 passed in 31.92s
git diff --check main...HEAD: PASS
```

The fixture suite proves empty/missing repository rejection, exact plan/provenance identity,
scheduled capability execution, strict degradation rejection, partial publication without
LocalServices, retained AOSP gating, source mutation rejection, deterministic fingerprints,
atomic live-database preservation, and byte-for-byte local configuration preservation during
upgrade.

## Code-review correction

Final review found that provenance originally retained the staging absolute path for the
scope report. After publication, a later verifier could no longer find that path and skipped
the raw report digest check. Publication also compared scope metadata between the database
and manifest without re-reading the staged report immediately before moving live files.

Commit `7abbe1a` adds two regression tests and closes both gaps:

- provenance validation relocates the report by basename into the published workspace and
  rejects missing or digest-mismatched scope evidence;
- publication revalidates report presence, self-fingerprint, status, build ID, and raw SHA-256
  before any live reports are moved.

## Real WSL partial-source smoke

The feature payload was installed into isolated paths so the existing live project and graph
were not modified:

```text
source: /home/ts/AndroidContextIntelligence-partial-source-v01
target: /home/ts/android-context-intelligence-partial-source-v01
repository: platform/frameworks/libs/systemui
include: frameworks/libs/systemui/displaylib/src
revision: 11e04f60f563aed48e4ec080bd7bde06bae1b2f3
revision state: clean
inventory: 7 files
inventory SHA-256: e8ddd5e1e75073a086e59a22a7d5f8f2d2a7f232e777d627a847b31321e38a9b
```

Observed rebuild evidence:

```text
source_scope_validation: preflight_passed
Imported 346 kotlin symbols
permission_validation: PASS
call_dataflow_validation: PASS; call_sites=0; paths=0
Runtime coverage: degraded: 4; supported: 1; unsupported: 1
source_scope_validation: passed
foreign_key_check: PASS
source-backed nodes: 653
published provenance revalidation: PASS
```

The published query returned:

```text
analysis_scope: partial
full_aosp_coverage: 0
warning: PARTIAL SOURCE GRAPH - NOT FULL AOSP
scope_report_sha256: 3c450d33c19b3d93e5a7937141d76d7654d74ef25f9bd63d00f1db79330fba44
```

Capability interpretation is explicit: Kotlin tags were supported; service registration,
permission semantics, call graph, and interprocedural dataflow were degraded because this
seven-file subset contained no required evidence or verified CodeQL database; Kotlin
inheritance was unsupported. These are coverage statements, not false zero-result claims.

## Acceptance boundaries

```text
Fixture partial publication: PASS
AOSP-profile regression: PASS (automated fixture)
Atomic failure preservation: PASS
WSL local partial smoke: PASS
Full AOSP build/graph acceptance: NOT RUN
```

The partial smoke does not prove complete AOSP coverage, runtime behavior, native Binder,
C/C++/Rust semantics, or real CodeQL call/dataflow extraction. Those capabilities require the
corresponding source repositories, build environment, or parser/database inputs.
