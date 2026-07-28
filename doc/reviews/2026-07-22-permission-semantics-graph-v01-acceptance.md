# Permission Semantics Graph v0.1 Acceptance

Implementation started: 2026-07-22  
Final verification: 2026-07-28

## Scope

This record covers the deterministic Permission semantic fact model, XML and
Java/Kotlin extraction, graph materialization, report/database validation, and
atomic publication gate described by the approved design and current plan.

## Repository verification

The final feature worktree was verified from WSL with the canonical project
virtual environment:

```text
root test suite:       51 passed
project test suite:   111 passed
Python compileall:    PASS
all tracked .sh:      bash -n PASS
git diff --check:     PASS
```

The project count includes the regression that proves a
`PERMISSION:android.permission.MANAGE_USB` node is insufficient: the strong
AOSP gate requires an active `DECLARES_PERMISSION` edge to that node.

## Real AOSP verification

The disposable target was:

```text
/home/ts/android-context-intelligence-permission-v01-acceptance-2
```

The source checkout was `/home/ts/aosp`; the existing
`/home/ts/android-context-intelligence` deployment was not used as the
acceptance target.

The canonical `frameworks/base` scope includes:

```toml
include = ["core", "services", "packages", "data"]
```

The `data` root is required for
`frameworks/base/data/etc/privapp-permissions-platform.xml`.

### Build 1

```text
build_id:    20260722T111652Z-48284-a94e89ac
started_at:  2026-07-22T11:16:52Z
verified_at: 2026-07-22T19:10:22Z
fingerprint: 54c9f8afdabe9b34702b30e6f85e5acf6b2ea81ddbd18bc2d2fd8927ea012c8b
```

### Build 2

```text
build_id:    20260728T011213Z-54869-8c7fefb3
started_at:  2026-07-28T01:12:13Z
verified_at: 2026-07-28T04:13:40Z
fingerprint: 54c9f8afdabe9b34702b30e6f85e5acf6b2ea81ddbd18bc2d2fd8927ea012c8b
```

The two independently published builds have the same Permission semantic
fingerprint. The second publication left no `.publish-journal.json` or
unfinished staging batch.

## Strong evidence gate

The accepted command is:

```bash
python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --require-aosp-evidence \
  --fingerprint
```

It requires:

- a declaration edge for `android.permission.MANAGE_USB`;
- request evidence;
- privapp allow or deny evidence;
- default-permissions evidence retaining `fixed` or `whitelisted`;
- method-level check and enforce evidence;
- valid endpoint types, no duplicate active semantic edges, and no broken
  foreign keys.

Observed examples:

```text
DECLARES_PERMISSION:
  AndroidManifest.xml -> android.permission.MANAGE_USB

ALLOWLISTS_PRIVILEGED_PERMISSION:
  com.android.providers.telephony -> android.permission.MANAGE_USERS
  source: frameworks/base/data/etc/privapp-permissions-platform.xml

DEFAULT_GRANTS_PERMISSION:
  com.android.printservice.recommendation
    -> android.permission.ACCESS_LOCAL_NETWORK
  fixed: false
```

## Published graph counts

```text
ANDROID_PACKAGE                         74
PERMISSION                            1187
ALLOWLISTS_PRIVILEGED_PERMISSION       733
CHECKS_PERMISSION                      677
DECLARES_PERMISSION                   1168
DEFAULT_GRANTS_PERMISSION                1
ENFORCES_PERMISSION                    674
REQUESTS_PERMISSION                   1030
REQUIRES_PERMISSION                   2761
```

No `DENIES_PRIVILEGED_PERMISSION` edge exists in this enabled checkout. The
strong gate intentionally accepts an allow or deny policy edge because the
available platform policy can legitimately contain only one side.

## Diagnostics retained

The report does not hide incomplete heuristic coverage:

```text
files scanned: Java 10353, Kotlin 7449, XML 12734
XML candidates: manifest 158, privapp 16, default-permissions 1
malformed XML: 0
task failures: 0
declaration conflicts: 22
duplicate facts: 13
unresolved method owners: 271
unresolved permission expressions: 307
unsupported constructs: 19
```

Conflicts and unresolved expressions are evidence-quality findings in v0.1,
not silently synthesized facts.

## Upgrade caveat

The current installer preserves a deployment's local
`config/source_roots.toml` during upgrade. Existing installations must add
`data` to the `frameworks/base` include list manually. Canonical-default/local
override migration is a P1 item in the next architecture plan.

## Semantic boundaries

- Allowlist is policy eligibility, not a runtime grant.
- Check observes or returns; enforce denies by raising an error.
- Missing method owners are diagnostics and never become dangling edges.
- Validation runs against staged artifacts before atomic publication.
