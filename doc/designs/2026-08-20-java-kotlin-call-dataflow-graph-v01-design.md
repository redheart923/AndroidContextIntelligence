# Java/Kotlin Call and Interprocedural Dataflow Graph v0.1 Design

Date: 2026-08-20

## 1. Outcome

This design adds a CodeQL-backed semantic enhancement layer to Android Context
Intelligence. It materializes a repository-aware Java/Kotlin call graph and a
bounded System Service security dataflow graph without replacing the existing
Ctags, Binder, Service, Permission, provenance, collision, staging, or
determinism contracts.

The design makes uncertainty explicit. A statically unique call target is a
`MUST_CALL`; a legal polymorphic target is a `MAY_CALL`; an unresolved call is
preserved with a reason and is never promoted to an arbitrary target. Runtime
traces may later add `OBSERVED_CALL` evidence for a particular device and
snapshot without overwriting static facts.

## 2. Confirmed scope

### 2.1 Included

- Java and Kotlin from repositories enabled by the workspace plan.
- A separately prepared, content-addressed CodeQL `java-kotlin` database.
- A complete call-site export for source methods observed by that CodeQL
  database.
- Method-level derived call relationships for convenient graph traversal.
- System Service security scenarios covering:
  - Binder/AIDL entry parameters;
  - Permission, AppOps, and cross-user checks;
  - Binder caller UID/PID identity;
  - `clearCallingIdentity()` and `restoreCallingIdentity()` transitions;
  - configured sensitive System Service sinks.
- Separate data propagation, control protection, and identity-transition facts.
- Versioned corrections supporting `suppress`, `replace`, `annotate`, and `add`.
- Schema migrations, extraction evidence, quality reports, semantic
  fingerprints, graph diffs, and atomic publication.
- Initial real-AOSP acceptance targets `services` and `SystemUI`, including AMS,
  PMS, and a Kotlin SystemUI representative chain.

### 2.2 Excluded from v0.1

- C/C++, Rust, HIDL, and Native Binder semantics.
- A claim that a static `MAY_CALL` target was observed at runtime.
- General all-variable-to-all-variable dataflow export.
- Intent, Bundle, filesystem, shell command, and system-property source/sink
  packs beyond facts needed by the selected System Service scenarios.
- Whole-AOSP semantic coverage when only selected build targets were compiled.
- Replacing CodeQL facts with AI-generated facts.
- Direct edits to the live SQLite database.

## 3. Architecture

```text
AOSP source + product/variant + build targets
                 |
                 v
       scripts/prepare_codeql.sh
                 |
                 v
  verified content-addressed CodeQL DB
                 |
      +----------+-----------+
      |                      |
      v                      v
 call-site query pack   security query pack
      |                      |
      +----------+-----------+
                 v
        normalized fact JSONL
                 |
                 v
 semantic identity reconciliation
                 |
                 v
      staging typed tables + graph
                 |
                 v
 structural / fixture / AOSP / correction /
 provenance / collision / fingerprint gates
                 |
                 v
       atomic publication to live DB
```

CodeQL is an enhancement layer. If no compatible database is supplied, the
existing graph can still rebuild, but `call_graph` and
`interprocedural_dataflow` are reported as unsupported. Strict capability mode
rejects publication instead of accepting degraded coverage.

## 4. CodeQL database lifecycle

### 4.1 Preparation is independent from graph rebuild

`scripts/prepare_codeql.sh` creates and verifies the database using a real
manual AOSP/Soong build. Java `none` mode is not allowed because it excludes
Kotlin and lacks the selected build's complete dependency and generated-source
context.

The preparation command accepts:

```text
--aosp-root
--codeql-bin
--product
--variant
--build-target (repeatable)
--threads
--ram-mb
--cache-root
```

The default acceptance target set is `services` and `SystemUI`. Product and
variant have no hidden default: preparation requires both explicitly or reads
them from an explicit checked configuration file.

### 4.2 Cache identity

The CodeQL database cache key hashes:

- repository revisions, dirty states, and source inventories;
- product, variant, and ordered build targets;
- build command and relevant environment identity;
- CodeQL CLI and Java/Kotlin extractor versions;
- generated-source inventory and finalized database metadata.

Query packs do not alter a CodeQL database, so their versions are not part of
the database key. Query-result caches separately hash the CodeQL database
fingerprint, query-pack lock, query ID, query version, and query parameters.

The cache is published only after `codeql database finalize` and a metadata
validation pass. A rebuild rejects a database whose source revision,
inventory, product, variant, target set, language, or extractor identity does
not match its manifest.

### 4.3 Actual coverage, not configured intent

Preparation records both planned targets and CodeQL-observed source files,
types, and methods. Coverage is reported against the observed build set. Files
in an enabled repository that were not compiled cannot be reported as
call-graph supported.

## 5. Semantic identity reconciliation

Existing logical `JAVA_METHOD` and `KOTLIN_METHOD` nodes remain the stable query
entry points. CodeQL produces independent definition evidence; it never
overwrites a Ctags definition or logical node.

A semantic symbol key contains:

```text
language
package
declaring type hierarchy
callable kind (method / constructor / lambda / initializer)
name
normalized erased parameter types
```

Return type, generic signature, source span, repository, and compiler identity
are matching evidence but are not used to pretend that JVM overload identity
depends on return type.

Resolution outcomes are:

- `unique`: one CodeQL definition resolves to one logical method;
- `ambiguous`: multiple legal logical candidates are retained;
- `unmatched`: no compatible logical method exists;
- `synthetic`: compiler-generated callable retained as CodeQL-only evidence.

Only `unique` endpoints may back accepted `MUST_CALL`, `MAY_CALL`, dataflow, or
guard graph edges. Ambiguous and unmatched rows remain diagnostics. Synthetic
callables may participate inside a typed path but cannot silently masquerade as
a user-declared logical method.

## 6. Database evolution

### 6.1 Compatibility rule

The existing `node` and `edge` tables remain backward-compatible. New dense
facts use typed extension tables with foreign keys to graph nodes. Fields used
for joins, filtering, uniqueness, ordering, validation, or version checks are
real columns. `properties_json` is reserved for low-frequency, query-specific
diagnostics.

Schema versioning uses both `PRAGMA user_version` and an auditable
`schema_migration` table. A full rebuild creates the latest schema directly.
Migration and validation run only in staging; a migration never mutates the
published live database in place.

### 6.2 New tables

#### `extraction_run`

```text
run_id PRIMARY KEY
capability
database_fingerprint
source_fingerprint
product
variant
build_targets_json
codeql_version
extractor_version
query_pack_lock_hash
observed_file_count
observed_method_count
status
started_at
completed_at
properties_json
```

#### `semantic_definition`

```text
definition_id PRIMARY KEY REFERENCES node(node_id)
run_id REFERENCES extraction_run(run_id)
logical_method_id REFERENCES node(node_id) NULL
semantic_symbol_key
language
callable_kind
repository
source_path
line_start
column_start
line_end
column_end
resolution_status
content_hash
properties_json
```

#### `call_site`

```text
call_site_id PRIMARY KEY REFERENCES node(node_id)
run_id REFERENCES extraction_run(run_id)
caller_method_id REFERENCES node(node_id)
repository
source_path
line_start
column_start
line_end
column_end
expression_hash
dispatch_kind
resolution_status
candidate_count
content_hash
properties_json
```

#### `call_target`

```text
call_site_id REFERENCES call_site(call_site_id)
callee_method_id REFERENCES node(node_id)
relation_kind CHECK relation_kind IN ('must', 'may')
evidence_id REFERENCES extraction_evidence(evidence_id)
content_hash
PRIMARY KEY(call_site_id, callee_method_id, relation_kind)
```

#### `program_value`

```text
value_id PRIMARY KEY REFERENCES node(node_id)
run_id REFERENCES extraction_run(run_id)
owner_method_id REFERENCES node(node_id)
value_kind CHECK value_kind IN
  ('parameter', 'return', 'field_read', 'field_write', 'expression')
parameter_index NULL
declared_type
repository
source_path
line_start
column_start
line_end
column_end
expression_hash
content_hash
properties_json
```

`PROGRAM_VALUE` nodes give method parameters, returns, field accesses, and
source expressions stable typed identities. They prevent a path from reducing
“parameter 2 of this method” or “the value returned at this call site” to a
method-level approximation.

#### `dataflow_path`

```text
path_id PRIMARY KEY REFERENCES node(node_id)
run_id REFERENCES extraction_run(run_id)
scenario_id
source_value_id REFERENCES program_value(value_id)
sink_value_id REFERENCES program_value(value_id)
path_kind
confidence_class
step_count
path_fingerprint
evidence_id REFERENCES extraction_evidence(evidence_id)
status
properties_json
```

#### `dataflow_step`

```text
path_id REFERENCES dataflow_path(path_id)
ordinal
value_id REFERENCES program_value(value_id)
step_kind
repository
source_path
line_start
column_start
line_end
column_end
message
content_hash
PRIMARY KEY(path_id, ordinal)
```

#### `security_trace`

```text
trace_id PRIMARY KEY REFERENCES node(node_id)
run_id REFERENCES extraction_run(run_id)
scenario_id
entry_method_id REFERENCES node(node_id)
sink_call_site_id REFERENCES call_site(call_site_id)
guard_count
identity_transition_count
trace_fingerprint
status
properties_json
```

`security_trace_step` stores ordered references to call sites, dataflow paths,
guards, and identity transitions. It composes existing facts and does not
relabel a control dependency as dataflow.

#### `extraction_evidence`

```text
evidence_id PRIMARY KEY
run_id REFERENCES extraction_run(run_id)
query_pack
query_id
query_version
raw_result_hash
raw_result_path
database_fingerprint
content_hash
properties_json
```

#### `fact_correction` and `correction_application`

The live database records the current correction definitions and their
application results. The canonical correction source is Git-managed TOML under
`project/config/corrections/`.

### 6.3 Graph projection

Each call-site row has a corresponding `CALL_SITE` node and:

```text
CALL_SITE -IN_METHOD-> logical caller method
CALL_SITE -MUST_CALL-> logical callee method
CALL_SITE -MAY_CALL-> logical callee method
```

Method-level `CALLS` edges are derived convenience facts. Their properties
include `relation_kind`, supporting call-site count, candidate count, run ID,
and evidence IDs. Consumers that require source precision must query call sites,
not only method-level `CALLS`.

Dataflow and trace projection uses:

```text
PROGRAM_VALUE -VALUE_IN_METHOD-> owning logical method
DATAFLOW_PATH -FLOW_SOURCE-> PROGRAM_VALUE
DATAFLOW_PATH -FLOW_SINK-> PROGRAM_VALUE
sensitive CALL_SITE -GUARDED_BY-> check CALL_SITE
sensitive CALL_SITE -IDENTITY_CLEARED_BY-> clear CALL_SITE
sensitive CALL_SITE -IDENTITY_RESTORED_BY-> restore CALL_SITE
SECURITY_TRACE -TRACE_ENTRY-> Binder entry method
SECURITY_TRACE -TRACE_SINK-> sensitive CALL_SITE
```

## 7. Static call semantics

- `MUST_CALL` is emitted only when CodeQL proves one statically unique target,
  including direct static, constructor, `super`, private, and final dispatch
  cases supported by the query.
- `MAY_CALL` enumerates legal polymorphic targets. Every candidate remains
  explicit; candidate ordering carries no likelihood meaning.
- `UNRESOLVED_CALL` is represented by a call-site row with zero accepted
  targets and a structured reason such as missing dependency, ambiguous symbol
  reconciliation, unsupported callable, or incomplete build coverage.
- `OBSERVED_CALL` is reserved for a future runtime snapshot and must include
  device/build/snapshot identity.

“Precise” therefore means exact preservation of what static evidence proves and
of what it cannot prove, not an assertion of a single runtime implementation.

## 8. System Service security semantics

### 8.1 Separate fact classes

- `FLOWS_TO` represents value propagation through parameters, returns, fields,
  and modeled library steps.
- `GUARDED_BY` represents a permission, AppOps, or cross-user check that
  dominates a sensitive operation with no accepted bypass path in the modeled
  control-flow graph.
- `IDENTITY_CLEARED_BY` and `IDENTITY_RESTORED_BY` represent Binder identity
  transitions and their pairing.
- `SECURITY_TRACE` composes call, flow, guard, and identity facts for display and
  querying.

### 8.2 Guard requirements

A check is not accepted as a guard merely because it appears earlier in the
same method. The query must establish the configured success/failure semantics
and control-flow dominance. Throwing enforcement calls, return-code AppOps
checks, and conditional user checks use separate query models. A check on only
one branch cannot guard a sink reachable through another branch.

### 8.3 Identity requirements

An identity transition records token creation, the region executed under
cleared identity, and restoration. A valid paired transition requires
restoration on all modeled normal and exceptional exits, normally through a
`finally` block. Unpaired or conditionally restored transitions remain
diagnostics and cannot be presented as safe pairing.

## 9. Corrections

### 9.1 Canonical format

Corrections are TOML files committed under:

```text
project/config/corrections/*.toml
```

Every correction requires:

```text
correction_id
action: suppress | replace | annotate | add
target_fact_uri
expected_content_hash
applicable_source_revision
reason
evidence_refs
author
approved_by
approval_ref
```

`replace` and `add` also require a complete typed replacement payload.
`annotate` may change confidence or attach reviewed explanation but cannot alter
endpoints. An `add` fact must identify external deterministic evidence and is
distinguishable from extractor-produced facts.

### 9.2 Application lifecycle

```text
draft -> validated -> active -> stale
```

The importer first preserves the raw extractor fact and then applies the
correction as a separate evidence layer. `suppress` changes the effective view
but does not delete original evidence. `replace` records `SUPERSEDES`;
conflicting retained evidence records `CONTRADICTS`.

A hash or source-revision mismatch makes the correction stale. Stale,
malformed, duplicate, or unapproved corrections fail strict publication.
Direct live-SQLite edits are unsupported and are detected by deployment and
fingerprint validation.

## 10. Validation and acceptance

### 10.1 Five validation layers

1. **QL fixtures:** positive and negative Java/Kotlin fixtures for overloads,
   inheritance, interface dispatch, anonymous classes, lambdas, Kotlin
   extensions, guard dominance, identity pairing, sources, and sinks.
2. **Structural validation:** foreign keys, stable IDs, source spans, ordered
   paths, accepted endpoints, duplicate facts, and extraction evidence.
3. **Identity quality:** eligible source method reconciliation is at least 95%;
   every call site is classified; accepted targets have unique logical
   endpoints; ambiguity and unmatched counts are reported.
4. **AOSP strong evidence:** fixed AMS, PMS, and Kotlin SystemUI samples must
   match expected calls and security semantics; negative samples must remain
   absent.
5. **Determinism:** two clean builds with equal inputs must produce equal call,
   dataflow, security-trace, correction-view, and whole-graph fingerprints.

### 10.2 Hard publication gates

- zero foreign-key or typed-schema violations;
- zero accepted call edges with ambiguous or missing endpoints;
- 100% of accepted paths have ordered steps, source/sink evidence, query
  identity, and source locations for source-backed steps;
- zero false positives in committed negative fixtures;
- required AMS/PMS/SystemUI strong evidence exists;
- CodeQL database manifest matches source/build configuration;
- no stale correction in strict capability mode;
- symbol collision and provenance validation still pass;
- fingerprint comparison passes for final acceptance.

An empty result is not proof of absence. Missing database coverage, unsupported
query constructs, and unresolved identity are reported separately.

## 11. Failure and publication behavior

All imported facts are written to the existing staging build. A query failure,
manifest mismatch, validation failure, stale correction, collision, or
fingerprint failure leaves the current live database unchanged. Diagnostic raw
results may be retained with the existing failed-staging option.

Default mode permits the pre-existing graph to rebuild without CodeQL while
truthfully reporting the two new capabilities as unsupported or stale. These
commands make them mandatory:

```bash
bash scripts/rebuild_all.sh \
  --codeql-db /path/to/verified-db \
  --strict-capability call_graph

bash scripts/rebuild_all.sh \
  --codeql-db /path/to/verified-db \
  --strict-capability interprocedural_dataflow
```

## 12. History and graph diffs

`data/android_context.db` contains only the current verified effective graph.
Every build retains an immutable manifest, coverage report, quality report,
query result hashes, correction application report, and semantic fingerprints.
Complete SQLite snapshots are optional and controlled by an explicit retention
flag.

`scripts/graph_diff.py` compares two build evidence directories or databases
and reports added, removed, changed, suppressed, superseded, and stale facts.
Diff identity uses typed stable IDs and content hashes, not timestamps.

## 13. User-facing workflow

```bash
# Heavy, infrequent preparation
bash scripts/prepare_codeql.sh \
  --aosp-root /home/ts/aosp \
  --codeql-bin /opt/codeql/codeql \
  --product aosp_cf_x86_64_phone \
  --variant userdebug \
  --build-target services \
  --build-target SystemUI

# Normal atomic graph rebuild using the verified cache
bash scripts/rebuild_all.sh \
  --codeql-db /path/printed/by/prepare_codeql.sh

# Strong verification
python -m workspace.call_dataflow_validation \
  --db data/android_context.db \
  --report data/raw/codeql/call-dataflow-report.json \
  --require-aosp-evidence \
  --fingerprint

# Compare two accepted builds
python scripts/graph_diff.py \
  --before data/history/20260820T010000Z \
  --after data/history/20260820T020000Z
```

## 14. Implementation boundaries

The implementation must follow TDD and use independent Git commits for schema,
CodeQL preparation, query packs, identity reconciliation, call import,
security-path import, corrections, validation, staging integration,
documentation, and real-AOSP acceptance. The feature is not complete merely
because unit fixtures pass; final acceptance requires a real Java/Kotlin AOSP
CodeQL database, the fixed strong-evidence samples, and two-build fingerprint
comparison.

## 15. References

- [GitHub CodeQL: code scanning for compiled languages](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/manage-your-configuration/codeql-for-compiled-languages)
- [GitHub CodeQL CLI: database create](https://docs.github.com/en/code-security/reference/code-scanning/codeql/codeql-cli-manual/database-create)
