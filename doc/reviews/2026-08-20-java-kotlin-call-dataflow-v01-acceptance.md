# Java/Kotlin Call and Interprocedural Dataflow Graph v0.1 Acceptance

Status: Implementation and fixture verification complete; real-AOSP acceptance blocked by
an incomplete AOSP checkout.

## Scope

- Precise Java/Kotlin call sites with `MUST_CALL`, `MAY_CALL`, and explicit unresolved status.
- Bounded System Service interprocedural dataflow, guard, and Binder identity facts.
- Typed semantic tables, extraction evidence, Git-managed corrections, effective views,
  semantic fingerprints, graph diff, and atomic publication.
- Acceptance targets: AMS, PMS, and Kotlin SystemUI in a verified `java-kotlin` CodeQL DB.

## Static and fixture verification

Executed from WSL against the Git worktree on 2026-08-20:

```text
project suite: 223 passed in 24.52s
root suite: 57 passed in 47.84s
CodeQL fixtures: 4 passed in 3m17s
```

These results verify Python contracts, typed schema, query normalization, identity
reconciliation, materialization, corrections, validation, atomic publication, deployment
payload, and documentation fixtures. They are not substitutes for a real CodeQL database.

The dataflow fixture now contains a four-node interprocedural path through a helper method.
The production query is a CodeQL path-problem; BQRS is interpreted as SARIF v2.1.0 and
ordered `codeFlows/threadFlowLocations` are materialized as `dataflow_step` rows. Query
source hashes participate in result-cache keys. Strong-evidence configuration now binds
exact semantic caller/callee keys, source paths, relation kinds, security-trace semantics,
and committed absence checks instead of accepting representative class existence.

## Real-AOSP evidence

The attempted database preparation used CodeQL 2.26.3 and requested
`aosp_cf_x86_64_phone-userdebug`, `services`, and `SystemUI`. It stopped before extraction
because `/home/ts/aosp/build/envsetup.sh` does not exist. The checkout's manifest is
`android-17.0.0_r1`, while `repo list` reports only:

```text
frameworks/base
frameworks/libs/systemui
frameworks/native
```

No cached project objects exist for `platform/build`, `platform/build/soong`, build-tools,
or JDK 17. The implementation now rejects this state before launching CodeQL and preserves
the underlying process stdout/stderr when a traced build fails. `build-mode=none` was not
used because it excludes Kotlin and would violate the approved acceptance scope.

Pending after a buildable checkout is available: database fingerprint, observed Java/Kotlin
counts, reconciliation metrics, representative AMS/PMS/SystemUI queries, correction report,
and two-build fingerprints.

## Residual boundaries

C/C++/Rust, Native Binder, whole-program unbounded taint, and runtime observed calls are not
part of v0.1. An empty static result is not proof of runtime absence.
