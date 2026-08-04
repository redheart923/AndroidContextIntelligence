# Trustworthy Multi-Source Ingestion v0.1 Acceptance

Date: 2026-08-04

## Outcome

Accepted. Tasks 1 through 6 are implemented as independent Git milestones, and
Task 7 closes the repository, installation, real-source, deterministic-build,
Vendor, publication-recovery, and performance gates. The accepted result does
not broaden the explicit parser and graph-domain non-goals listed below.

## Accepted milestone commits

| Task | Feature commit | Merge commit | Evidence boundary |
|---|---|---|---|
| 1. Capability quality and runtime evidence | `06a87b3`, `4ed5ae8` | `ed19bc9` | Quality classes and observed-evidence gates |
| 2. Canonical defaults and local migration | `bdf18fd` | `4be462f` | Defaults/local split and transactional upgrade migration |
| 3. Repository-scoped definitions | `e30c441` | `79d3b47` | Definition preservation and collision publication gate |
| 4. Reproducible provenance | `02e255d` | `f6cf9c8` | Revision, dirty state, inventory/config/tool identity |
| 5. Vendor artifact staged ingestion | `19fb1c1` | `1aa2e2d` | Content-addressed preparation and staged publication |
| 6. Service pipeline optimization | `2b1cc38` | `82cc677` | Candidate indexing, digest cache, correctness profile |

## Repository and installation gates

The acceptance worktree is based on `82cc677`. Installation was exercised in
the isolated target `/home/ts/aci-task7-install-final` against
`/home/ts/aosp`:

- fresh installation completed without replacing an existing target;
- verify-only reported `payload verification: PASS (90 managed files)`;
- upgrade preserved `data/task7-runtime-marker` and the AOSP root;
- legacy `config/source_roots.toml` was migrated to
  `config/source_roots.local.toml`;
- rollback source was retained at
  `/home/ts/.install-rollback-aci-task7-install-final-43945b88836c4c4da53d233131b59fcc`;
- verify-only passed again after upgrade;
- invalid legacy configuration and injected promotion failure both preserved
  the original target (`2 passed`).

After the Task 7 commit, an upgrade correctly rejected the temporary `.venv`
symlink used only to accelerate the AOSP builds and restored the target. The
fixture was returned to a real venv directory; the committed payload then
upgraded successfully and verify-only reported
`payload verification: PASS (92 managed files)`. The runtime marker, local
configuration, installed `scripts/graph_fingerprint.py`, and accepted live
`GRAPH_BUILD` identity `20260804T012356Z-1026-53fed488` were all present after
that upgrade. The symlink rejection is retained as additional rollback/safety
evidence; the installer contract was not weakened to accommodate the fixture.

The isolated target and rollback directory are runtime evidence only. They are
not canonical source and are not copied back into the Git repository.

## Real AOSP input

Both graph builds use the same clean source and canonical configuration:

| Input | Value |
|---|---|
| Repository | `platform/frameworks/base` |
| Workspace path | `frameworks/base` |
| Revision | `94b4c163b7dfe5ce3607f7bb8456f9573f7de57d` |
| Dirty | `false` |
| Inventory files | 33,273 |
| Inventory SHA-256 | `5b6b59991c06feb43fde0702c1d56574ea1ed09a51ad5368eccb9966bde730c4` |
| Source config SHA-256 | `a3e286de20877092b4b85bf599cb4e09acd1217667290b963ca9c795fb3652d2` |
| Local config SHA-256 | `003923d4fb1ef043bff8c5389fa575f286d9cae7b6bf53ded0bf7377bb5f1e03` |
| Parser registry SHA-256 | `6ddc3b3188137ccb56e2c9039fd0d59ddc87738b6270023458b1582084543772` |
| Tools | Python 3.12.3, SQLite 3.45.1, Universal Ctags 5.9.0 |

The first clean staged build was published as
`20260803T084542Z-3038-bb9a119c`. The second started from another empty staged
database and was published as `20260804T012356Z-1026-53fed488`. Both manifests
have `status=verified`; source revision, inventory, configuration, registry,
and tool identities match. Build identity and timestamps differ as expected.

## Determinism gates

`project/scripts/graph_fingerprint.py` hashes every stable node and edge field,
excluding only `GRAPH_BUILD` identity, edges attached to it, and `updated_at`.
This is deliberately broader than the Permission-specific fingerprint.

| Gate | Build 1 | Build 2 | Result |
|---|---:|---:|---|
| Nodes | 1,162,418 | 1,162,418 | MATCH |
| Edges | 1,571,908 | 1,571,908 | MATCH |
| Permission semantic edges | 7,044 | 7,044 | MATCH |
| Permission fingerprint | `8ade1595dbcd64552e1dfa121f5c0118181294fd84703735da8975c287ed57b5` | `8ade1595dbcd64552e1dfa121f5c0118181294fd84703735da8975c287ed57b5` | MATCH |
| Whole-graph fingerprint | `c0aa4f0c819fe74ea005b094f10d78d0ab7f4e6dc09c08c9ba13071b96874f39` | `c0aa4f0c819fe74ea005b094f10d78d0ab7f4e6dc09c08c9ba13071b96874f39` | MATCH |

Permission validation was run with `--require-aosp-evidence`; a basic schema or
foreign-key pass alone is not counted as real-source acceptance.

## Runtime coverage truthfulness

The real build reports 9 `supported`, 9 `unsupported`, and 1 `degraded`
capability entries. In particular:

- XML Permission is `semantic`, with 2,932 observed facts/edges;
- Java Permission is `heuristic`, with 4,077 observed facts/edges;
- Kotlin Permission is `heuristic`, with 35 observed facts/edges;
- Kotlin service registration is explicitly `degraded` because required
  evidence was not observed;
- Kotlin inheritance and C/C++ native semantics remain `unsupported`.

These statuses are intentional evidence boundaries, not failures hidden behind
a green strict Permission gate.

## Permission evidence

The 7,044 published Permission edges are composed of:

| Edge type | Count |
|---|---:|
| `ALLOWLISTS_PRIVILEGED_PERMISSION` | 733 |
| `CHECKS_PERMISSION` | 677 |
| `DECLARES_PERMISSION` | 1,168 |
| `DEFAULT_GRANTS_PERMISSION` | 1 |
| `ENFORCES_PERMISSION` | 674 |
| `REQUESTS_PERMISSION` | 1,030 |
| `REQUIRES_PERMISSION` | 2,761 |

The representative-AOSP validation gate and SQLite foreign-key gate both pass.

## Symbol, Vendor, publication, and performance gates

- The real source collision report is empty (`[]`); strict publication did not
  select an ambiguous definition.
- A canonical Vendor fixture build used a fake versioned JADX executable and a
  `services.jar`, then verified one prepared artifact, its SHA-256, one emitted
  source file, and its digest in the published build provenance.
- The interrupted-publication fixture stopped after report publication with
  the old database still live; journal recovery returned `rolled_back` and
  restored the old matching report batch.
- The real Service profile retained activity/package/48 LocalServices, the
  fingerprint `3fb946c220dabcb873d5e5a9c8f892db53eaa2d3be7560407dee44acd49bad86`,
  and identical diagnostics across cold and warm runs.
- Service elapsed time improved from 719.396 s to 8.163 s (711.233 s,
  98.865%); both runs examined 14,241 files and the indexed candidate set was
  4,356 files, with 1,119 excluded before model construction.
- The second independent staged build also reused 4,356/4,356 Service cache
  entries and completed that graph layer in 8.440 s without changing counts.

## Test and static-analysis gates

| Gate | Result |
|---|---:|
| Root installation/repository tests | 54 passed |
| Canonical project tests | 145 passed |
| Vendor fixture plus interrupted publication | 2 passed |
| Config migration failure plus promotion rollback | 2 passed |
| `python -m compileall -q project scripts` | PASS |
| Bash syntax for every tracked `*.sh` | PASS |
| `git diff --check` | PASS |

## Review findings and residual boundaries

The repository-wide review found no blocking issue in the implemented v0.1
scope. The following are explicit residual boundaries rather than accepted
semantic coverage:

- Kotlin uses tag/heuristic parsing and has no inheritance semantics;
- C/C++, Rust, HIDL, native Binder, Soong Build Graph, runtime traces, and test
  result graphs are not implemented;
- unresolved AIDL, inheritance, and Service candidates remain diagnostics and
  must not be interpreted as complete Android platform coverage;
- the full-graph fingerprint proves deterministic stored semantics for equal
  inputs; it does not replace capability-specific correctness validation.

## Final decision

Trustworthy Multi-Source Ingestion v0.1 is accepted for local merge to `main`.
Future graph domains must reuse the established staged publication, provenance,
capability-evidence, collision, and deterministic acceptance contracts.
