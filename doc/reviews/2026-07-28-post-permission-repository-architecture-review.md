# Post-Permission Repository Architecture Review

Date: 2026-07-28

## Outcome

Permission Semantics Graph v0.1 closes the previously identified permission
fact gap and uses the staged database publication path. It does not close the
remaining trust boundary for every source type. The next milestone must make
parser coverage, configuration upgrades, symbol identity, provenance, and
Vendor ingestion truthful and transactional before adding more graph domains.

## Evidence reviewed

- canonical tracked payload under `project/`;
- fresh and upgrade installer implementation;
- workspace discovery, parser registry, execution planner, and graph pipelines;
- staged build and atomic publication implementation;
- Java, Kotlin, AIDL, inheritance, service, and permission reports;
- real `/home/ts/aosp` rebuild and Permission validation evidence;
- Vendor decompilation/import entry point and its documented limitations.

## Findings

### P1 — Vendor import bypasses the publication transaction

`project/scripts/import_vendor.sh` writes directly to
`data/android_context.db`. It does not share the rebuild lock, does not build a
staged database, and does not run the publication gates. Its decompilation
cache is keyed only by output-directory existence, while JADX failures are
allowed to continue. The script also hard-codes a local JADX path and sends one
tag stream through both Java and Kotlin imports.

Impact: a partial or stale decompilation can mutate the only live graph and
cannot be tied reliably to an artifact digest or decompiler version.

Required direction: Vendor artifacts become immutable inputs outside `data/`;
artifact digest, JADX version/options, decompilation status, and source
provenance enter the staged build manifest. Vendor graph changes publish only
through `rebuild_all.sh`.

### P1 — Upgrade preservation can mask canonical coverage fixes

`scripts/install_project.py` preserves `config/source_roots.toml` wholesale
during upgrade. This protects local repository choices, but it also restores an
old canonical default over a new payload. For example, the Permission acceptance
fix that adds `frameworks/base/data` is present on a fresh install but is not
automatically inherited by an existing deployment with a preserved config.

Impact: fresh and upgraded installations can execute different coverage rules
while reporting the same software version.

Required direction: split immutable defaults from local overrides and add an
explicit migration path. The installer must never silently discard either new
required defaults or local repository choices.

### P1 — Cross-repository qualified-name collisions overwrite definitions

`workspace.pipeline.run_java` and `run_kotlin` detect changed `source_path`
values after an import and write duplicate reports, but the graph node identity
is still the qualified name. A later repository can therefore replace the
source metadata of an earlier definition.

Impact: scan order selects a winner and the live graph loses one of the source
definitions. Reporting the collision after mutation does not preserve the
ambiguous fact.

Required direction: model repository-scoped definitions separately from
logical symbols, or reject ambiguous publication until an explicit resolution
policy exists.

### P1 — Parser capability is binary and can overstate semantic coverage

The registry declares Kotlin inheritance support, while current real builds can
produce zero Kotlin inheritance records because the tag stream has no
`inherits` field. The planner treats a scheduled heuristic tag parser as
equivalent to a semantic parser.

Impact: `--strict` proves that an entry point was scheduled, not that meaningful
coverage was produced.

Required direction: register capability quality (`semantic`, `heuristic`,
`tags_only`) and validate runtime evidence thresholds. A capability with no
usable output must be degraded or fail its strict gate.

### P1 — Source revision omits dirty state

`workspace.revisions.resolve_repository_revision` records `git rev-parse HEAD`
only. Two dirty source trees at the same commit receive the same revision even
when their graph facts differ.

Impact: a graph cannot be reproduced from the stored revision alone.

Required direction: record commit, dirty state, relevant source/config digest,
and parser/tool versions in the build manifest and fact provenance.

### P2 — Service registration dominates rebuild time

The service pipeline reads and indexes the full enabled Java source set and
performs repeated regular-expression and type-resolution work. Real rebuilds
spend materially longer in this stage than in the other semantic passes.

Impact: slow feedback increases the chance of interrupted acceptance runs and
makes multi-repository expansion expensive.

Required direction: profile before changing semantics, then add a candidate-file
index and cache immutable parsing results by source digest.

## What remains trustworthy today

- `project/` is the canonical readable payload;
- installation and graph publication use adjacent staging and rollback;
- Permission semantics are validated before publication;
- unsupported languages are reported instead of silently treated as parsed;
- the live database is not replaced when a staged build fails.

Those guarantees do not extend to the current Vendor import script or to
repository-qualified symbol collisions.

## Decision

Do not add Build, Runtime, or additional native graph domains next. Execute
[Trustworthy Multi-Source Ingestion v0.1](../plans/2026-07-28-trustworthy-multi-source-ingestion-v01-plan.md)
first.
