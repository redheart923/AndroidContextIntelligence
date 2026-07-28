# Permission Semantics Graph v0.1 Implementation Plan

> **Status: Completed and accepted on 2026-07-28.** See
> [the acceptance record](../reviews/2026-07-22-permission-semantics-graph-v01-acceptance.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable multi-repository Permission Semantics Graph covering manifest declarations and requests, privapp/default-permissions policy, and Java/Kotlin permission contracts, checks, and throwing enforcement.

**Architecture:** Context-specific XML parsers and a balanced Java/Kotlin lexical scanner emit immutable facts before any graph write. One deterministic materializer creates graph nodes and edges, while the workspace planner schedules `permission_semantics` and the existing staging build prevents failed imports from replacing live data.

**Tech Stack:** Python 3.11+, SQLite, Universal Ctags JSON, standard-library `xml.etree.ElementTree`, `tomllib`, Bash, pytest.

## Global Constraints

- Work only in `codex/permission-semantics-graph-v01`; commit every independently testable slice.
- `project/` is the only implementation source. Never edit or copy source back from `~/android-context-intelligence`.
- Follow `doc/designs/2026-07-21-permission-semantics-graph-v01-design.md` exactly.
- Do not execute `doc/plans/2026-07-17-permission-enforcement-graph-v01-plan.md`; it is superseded.
- Add no CodeQL, compiler frontend, or non-standard parsing dependency.
- Never represent a privapp allowlist entry as a completed runtime grant.
- Never emit `ENFORCES_PERMISSION` for non-throwing `check*Permission` APIs.
- Never guess a permission name or method owner.
- Preserve unresolved and unsupported evidence in deterministic reports.
- Import only into the canonical staging database; parser failure must not publish live data.
- Default mode reports semantic gaps. Strict modes fail only conditions defined by the approved design.
- Run tests with `/home/ts/android-context-intelligence/.venv/bin/python` from the Windows worktree mounted in WSL.
- Before every commit run focused tests, `git diff --check`, and inspect the staged file list.

## File responsibilities

```text
project/collectors/permission/model.py        immutable facts and diagnostics
project/collectors/permission/report.py       report aggregation and serialization
project/collectors/permission/xml_permission_importer.py
                                               manifest/privapp/default XML dialects
project/collectors/permission/lexical.py      layout-preserving balanced lexer
project/collectors/permission/resolver.py     permission constant resolution
project/collectors/permission/source_permission_scanner.py
                                               method-level Java/Kotlin semantics
project/collectors/permission/materializer.py deterministic graph writes
project/workspace/revisions.py                repository revision capture
project/workspace/multi_permission.py         scheduled task orchestration
project/workspace/permission_validation.py    report/DB validation and fingerprint
```

---

### Task 1: Import real Java and Kotlin method ranges

**Files:**
- Modify: `project/workspace/pipeline.py`
- Modify: `project/collectors/source/ctags_importer.py`
- Create: `project/tests/unit/test_ctags_method_ranges.py`
- Modify: `project/tests/integration/test_multi_repository_pipeline.py`

**Interfaces:**
- Consumes: Ctags records with `line` and optional `end`, plus the referenced
  source file when Ctags omits `end`.
- Produces: `parse_line_range(record: dict[str, object]) -> tuple[int | None, int | None]`,
  `resolve_line_range(record: dict[str, object]) -> tuple[int | None, int | None]`,
  and inclusive `line_end` values.
- Parser behavior verified against Universal Ctags 5.9.0: Java emits `end`,
  while Kotlin does not. Missing ranges therefore use a layout-preserving,
  balanced method-body fallback and remain one line when no body can be proven.

- [ ] **Step 1: Write failing unit tests**

```python
from collectors.source.ctags_importer import parse_line_range


def test_parse_line_range_uses_ctags_end() -> None:
    assert parse_line_range({"line": 10, "end": 27}) == (10, 27)


def test_parse_line_range_falls_back_without_invalid_range() -> None:
    assert parse_line_range({"line": 10}) == (10, 10)
    assert parse_line_range({"line": 10, "end": 4}) == (10, 10)
    assert parse_line_range({}) == (None, None)
```

Extend `test_multi_repository_pipeline.py` with multiline Java and Kotlin
methods and assert both database rows satisfy `line_end > line_start`. Add a
unit fixture proving that a Kotlin record without `end` resolves to the
matching closing brace while braces in comments and strings are ignored.

- [ ] **Step 2: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_ctags_method_ranges.py \
  project/tests/integration/test_multi_repository_pipeline.py
```

Expected: collection fails because `parse_line_range` is absent, or the integration assertion sees equal start/end lines.

- [ ] **Step 3: Request and normalize the end field**

Change both Java and Kotlin commands in `pipeline.py` from `--fields=+nKSEi` to:

```python
"--fields=+nKSEie"
```

Add and use this function for node, `DECLARED_IN`, and owner edge ranges:

```python
def parse_line_range(record: dict[str, object]) -> tuple[int | None, int | None]:
    start = record.get("line")
    if not isinstance(start, int):
        return None, None
    raw_end = record.get("end")
    end = raw_end if isinstance(raw_end, int) and raw_end >= start else start
    return start, end
```

When `parse_line_range()` returns a single-line range for a method record,
`resolve_line_range()` reads the referenced source, masks comments and string
literals without changing line layout, finds the body opening brace after the
balanced parameter list, and returns its matching closing-brace line. The
fallback is conservative: malformed or expression-body declarations keep the
single-line range and are reported unresolved by later semantic extraction.

- [ ] **Step 4: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_ctags_method_ranges.py \
  project/tests/unit/test_java_inheritance_importer.py \
  project/tests/integration/test_multi_repository_pipeline.py
git add project/workspace/pipeline.py project/collectors/source/ctags_importer.py \
  project/tests/unit/test_ctags_method_ranges.py \
  project/tests/integration/test_multi_repository_pipeline.py
git diff --cached --check
git commit -m "fix: import bounded source method ranges"
```

Expected: selected tests pass and the fixture method has a nontrivial inclusive range.

---

### Task 2: Define immutable permission facts and reports

**Files:**
- Create: `project/collectors/permission/model.py`
- Create: `project/collectors/permission/report.py`
- Modify: `project/graph/writer.py`
- Create: `project/tests/unit/test_permission_model.py`
- Create: `project/tests/unit/test_graph_writer_identity.py`

**Interfaces:**
- Produces: `PermissionFactKind`, `PermissionEvidence`, `PermissionFact`, `PermissionDiagnostic`, `ParseOutcome`, and `PermissionReport`.
- This layer is pure data and does not import `GraphWriter` or SQLite.

- [ ] **Step 1: Write failing fact identity tests**

```python
from collectors.permission.model import (
    PermissionEvidence,
    PermissionFact,
    PermissionFactKind,
)


def test_allow_and_deny_have_different_identities() -> None:
    evidence = PermissionEvidence(
        repository="frameworks/base",
        source_path="frameworks/base/data/etc/privapp-permissions-platform.xml",
        source_dialect="privapp_permissions",
        source_expression="android.permission.MANAGE_USB",
        line_start=None,
        line_end=None,
        source_revision="0123456789abcdef0123456789abcdef01234567",
        parser="xml_permission_importer",
        parser_version="permission-semantics-v0.1",
    )
    values = []
    for kind in (
        PermissionFactKind.ALLOWLISTS_PRIVILEGED_PERMISSION,
        PermissionFactKind.DENIES_PRIVILEGED_PERMISSION,
    ):
        values.append(PermissionFact(
            kind=kind,
            permission_name="android.permission.MANAGE_USB",
            package_name="com.android.systemui",
            owner_node_id=None,
            properties={},
            evidence=evidence,
        ).identity)
    assert values[0] != values[1]
```

Also assert report serialization is stable when outcomes are added in reverse order. In
`test_graph_writer_identity.py`, create two edges with the same type/endpoints/path/start
but different `fact_identity` properties and assert their `edge_id` values differ. Write
an edge with `source_revision="abc"` through a writer constructed with
`source_revision="fallback"` and assert the database stores `abc`.

- [ ] **Step 2: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_model.py
```

Expected: imports fail because the new modules are absent.

- [ ] **Step 3: Implement exact semantic types**

```python
class PermissionFactKind(StrEnum):
    DECLARES_PERMISSION = "DECLARES_PERMISSION"
    REQUESTS_PERMISSION = "REQUESTS_PERMISSION"
    ALLOWLISTS_PRIVILEGED_PERMISSION = "ALLOWLISTS_PRIVILEGED_PERMISSION"
    DENIES_PRIVILEGED_PERMISSION = "DENIES_PRIVILEGED_PERMISSION"
    DEFAULT_GRANTS_PERMISSION = "DEFAULT_GRANTS_PERMISSION"
    REQUIRES_PERMISSION = "REQUIRES_PERMISSION"
    CHECKS_PERMISSION = "CHECKS_PERMISSION"
    ENFORCES_PERMISSION = "ENFORCES_PERMISSION"
```

Use frozen dataclasses. `PermissionEvidence` contains repository, source path/range,
dialect, expression, source revision, parser, and parser version. `PermissionFact.identity`
is SHA-256 over canonical sorted JSON containing kind, permission, package, owner, all
evidence fields, and properties. `PermissionDiagnostic` contains category, reason code,
repository/path/range, expression, and message. `ParseOutcome` contains tuples of facts
and diagnostics plus counters.

- [ ] **Step 4: Make evidence-distinct edges independently addressable**

Change `Edge.edge_id` in `project/graph/writer.py` to hash canonical sorted
`properties_json` and `line_end` in addition to its current semantic type, endpoints,
source path, and start line:

```python
properties_json = json.dumps(self.properties, ensure_ascii=False, sort_keys=True)
identity = "|".join((
    self.edge_type, self.from_node_id, self.to_node_id,
    self.source_path or "", str(self.line_start or ""),
    str(self.line_end or ""), properties_json,
))
```

Permission materialization later stores `fact_identity` in every semantic edge. This
allows two conflicting declarations in one XML file to remain separate evidence. A full
graph rebuild is expected to regenerate existing edge IDs; no migration of a published
database is performed in place.

Add optional `source_revision: str | None = None` to `Node` and `Edge`. In both writer
methods use the fact-level value when supplied:

```python
effective_revision = node.source_revision or self.source_revision
# and, in upsert_edge:
effective_revision = edge.source_revision or self.source_revision
```

Include the effective revision in content hashes and persist it in the existing
`source_revision` column. Existing callers continue to use the writer fallback.

- [ ] **Step 5: Implement the required report schema**

`PermissionReport.add_outcome()` deduplicates facts by identity, preserves unique diagnostics, counts duplicates, detects declaration-property conflicts, and serializes these exact keys in stable order:

```python
REQUIRED_REPORT_KEYS = {
    "schema_version", "parser_version", "source_revisions",
    "repositories_scanned", "files_scanned_by_language",
    "xml_candidates_by_dialect", "xml_documents_parsed_by_dialect",
    "facts_and_edges_by_type", "duplicate_facts",
    "declaration_conflicts", "malformed_xml",
    "unresolved_permission_expressions", "unresolved_method_owners",
    "unsupported_constructs", "task_failures",
}
```

Use `schema_version="1.0"` and `parser_version="permission-semantics-v0.1"`. Do not include timestamps in semantic equality.

- [ ] **Step 6: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_model.py \
  project/tests/unit/test_graph_writer_identity.py
git add project/collectors/permission/model.py \
  project/collectors/permission/report.py \
  project/graph/writer.py project/tests/unit/test_permission_model.py \
  project/tests/unit/test_graph_writer_identity.py
git diff --cached --check
git commit -m "feat: define permission semantic facts"
```

Expected: identity, conflict, deduplication, and deterministic serialization tests pass.

---

### Task 3: Parse manifest, privapp, and default-permission XML

**Files:**
- Rewrite: `project/collectors/permission/xml_permission_importer.py`
- Create: `project/tests/unit/test_xml_permission_importer.py`

**Interfaces:**
- Consumes: fact types from Task 2.
- Produces: `XmlDialect`, `is_permission_xml_candidate(path: Path) -> bool`,
  `detect_xml_dialect(path: Path, root_tag: str | None = None) -> XmlDialect | None`,
  and `parse_permission_xml(path: Path, *, repository: str, source_path: str,
  source_revision: str) -> ParseOutcome`.

- [ ] **Step 1: Write manifest RED tests**

Create a namespaced `AndroidManifest.xml` fixture with a relative declaration and the three supported request tags. Assert:

```python
assert {(fact.kind.value, fact.permission_name) for fact in outcome.facts} == {
    ("DECLARES_PERMISSION", "com.example.permission.LOCAL"),
    ("REQUESTS_PERMISSION", "android.permission.CAMERA"),
    ("REQUESTS_PERMISSION", "android.permission.POST_NOTIFICATIONS"),
}
assert camera_request.properties["max_sdk_version"] == 32
assert camera_request.properties["uses_permission_flags"] == "neverForLocation"
```

- [ ] **Step 2: Write policy and error RED tests**

Use exact fixtures for:

```xml
<permissions><privapp-permissions package="com.example">
  <permission name="android.permission.CAMERA"/>
  <deny-permission name="android.permission.RECORD_AUDIO"/>
</privapp-permissions></permissions>
```

```xml
<exceptions><exception package="com.example">
  <permission name="android.permission.CAMERA" fixed="true" whitelisted="false"/>
</exception></exceptions>
```

Assert allow/deny are distinct, booleans normalize, invalid booleans emit `invalid_boolean_attribute`, malformed XML emits `malformed_xml`, and a layout resource containing `<permission>` produces no facts.

Also assert manifest declarations retain `protectionLevel`, `permissionGroup`, `label`,
`description`, and `knownSigner` expressions. An identified permission document with an
unknown direct child must increment `unknown_elements` without being treated as a task
failure.

- [ ] **Step 3: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_xml_permission_importer.py \
  project/tests/unit/test_permission_model.py
```

Expected: tests fail because the current importer uses generic `.//permission` and swallows parse errors.

- [ ] **Step 4: Implement dialect dispatch without broad exception handling**

```python
class XmlDialect(StrEnum):
    MANIFEST = "manifest"
    PRIVAPP = "privapp_permissions"
    DEFAULT = "default_permissions"


def parse_permission_xml(
    path: Path, *, repository: str, source_path: str, source_revision: str,
) -> ParseOutcome:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        return malformed_outcome(repository, source_path, source_revision, error)
    dialect = detect_xml_dialect(path, root.tag)
    if dialect is None:
        return ParseOutcome.empty(files_scanned=1)
    return PARSERS[dialect](root, repository=repository, source_path=source_path)
```

Manifest parsing uses direct manifest children for `permission`, `uses-permission`, `uses-permission-sdk-23`, and `uses-permission-sdk-m`. Privapp parsing walks only `privapp-permissions`; default parsing walks only `exception`. Unexpected exceptions propagate to the task boundary.

`is_permission_xml_candidate` uses exact manifest filename plus privapp/default permission
filename patterns and a bounded root-tag probe. It is an optimization only: selected
files still require dialect detection, and candidate/parsed/unknown-element counts enter
the report.

- [ ] **Step 5: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_xml_permission_importer.py \
  project/tests/unit/test_permission_model.py
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q project/tests
git add project/collectors/permission/xml_permission_importer.py \
  project/collectors/permission/report.py \
  project/tests/unit/test_xml_permission_importer.py \
  project/tests/unit/test_permission_model.py
git diff --cached --check
git commit -m "feat: parse Android permission XML dialects"
```

Expected: dialect, property, malformed-input, conflict, and full project tests pass.

---

### Task 4: Build the balanced lexer and permission resolver

**Files:**
- Create: `project/collectors/permission/lexical.py`
- Create: `project/collectors/permission/resolver.py`
- Create: `project/tests/unit/test_permission_lexical.py`
- Create: `project/tests/unit/test_permission_resolver.py`

**Interfaces:**
- Produces: `SourceConstruct`, `iter_permission_constructs(text: str) -> tuple[SourceConstruct, ...]`, `SourceBindings.from_text(text: str, language: str)`, and `resolve_permission_expression(expression: str, bindings: SourceBindings) -> Resolution`.
- `Resolution` contains `values: tuple[str, ...]`, `kind: str`, and `reason_code: str | None`.

- [ ] **Step 1: Write lexer RED tests**

Use Java and Kotlin fixtures containing multiline calls, nested arrays, escaped strings, block/line comments, and decoys inside strings. Assert only real constructs and exact line ranges:

```python
assert [(item.name, item.line_start, item.line_end) for item in constructs] == [
    ("RequiresPermission", 4, 7),
    ("enforceCallingPermission", 10, 13),
]
```

- [ ] **Step 2: Write resolver RED tests**

Cover literals, `android.Manifest.permission.CAMERA`, imported `Manifest.permission.CAMERA`, static imports, Java `static final String`, Kotlin `const val`, bounded alias chains, alias cycles, ambiguous imports, arrays, and computed expressions. Require reason codes `alias_cycle`, `ambiguous_import`, and `unsupported_expression` for unresolved cases.

- [ ] **Step 3: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_lexical.py \
  project/tests/unit/test_permission_resolver.py
```

Expected: imports fail because both modules are absent.

- [ ] **Step 4: Implement layout-preserving lexical masking**

Use a single-pass state machine for code, line comment, block comment, string, and character states. Replace ignored characters with spaces while preserving newlines. Balance delimiters for these structural names:

```python
ANNOTATIONS = frozenset({"RequiresPermission"})
CALLS = frozenset({
    "enforceCallingPermission", "enforceCallingOrSelfPermission",
    "enforcePermission", "enforceAnyPermissionOf", "enforceAllPermissions",
    "checkCallingPermission", "checkCallingOrSelfPermission",
    "checkPermission", "checkSelfPermission",
})
```

An unterminated recognized construct raises `LexicalError` carrying its start line; the orchestration layer converts it to a diagnostic.

- [ ] **Step 5: Implement bounded expression resolution**

Parse package/import/static-import declarations and same-file constant assignments. Resolve aliases recursively with:

```python
MAX_ALIAS_DEPTH = 16
```

Use a visited set for cycle detection. Return multiple values only for explicit arrays/collections. A bare unknown identifier must never be converted to `android.permission.<identifier>`.

- [ ] **Step 6: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_lexical.py \
  project/tests/unit/test_permission_resolver.py
git add project/collectors/permission/lexical.py \
  project/collectors/permission/resolver.py \
  project/tests/unit/test_permission_lexical.py \
  project/tests/unit/test_permission_resolver.py
git diff --cached --check
git commit -m "feat: resolve permission source expressions"
```

Expected: multiline, comment, string, constant, import, alias, cycle, array, and ambiguity tests pass.

### Task 5: Extract method-level Java and Kotlin semantics

**Files:**
- Create: `project/collectors/permission/source_permission_scanner.py`
- Create: `project/tests/unit/test_source_permission_scanner.py`
- Delete after migration: `project/collectors/permission/java_permission_scanner.py`

**Interfaces:**
- Consumes: Task 1 ranges, Task 2 facts, and Task 4 constructs/resolutions.
- Produces: `MethodRange`, `load_method_ranges(connection, source_path)`,
  `find_containing_method(methods, line_start, line_end)`, and
  `scan_permission_source(path, *, repository, source_path, source_revision,
  language, methods) -> ParseOutcome`.

- [ ] **Step 1: Write method ownership RED tests**

```python
methods = (
    MethodRange("JAVA_METHOD:A#one()", 10, 20),
    MethodRange("JAVA_METHOD:A#two()", 30, 40),
)
assert find_containing_method(methods, 15, 15).node_id.endswith("one()")
assert find_containing_method(methods, 25, 25) is None
```

Add overlapping ranges and assert the smallest complete containing interval wins.

- [ ] **Step 2: Write classification RED tests**

Fixtures must prove `@RequiresPermission` value/allOf/anyOf/conditional properties, `checkCallingPermission -> CHECKS_PERMISSION`, `enforceCallingPermission -> ENFORCES_PERMISSION`, no interprocedural inference for unknown wrappers, unresolved expressions/owners as separate diagnostics, and nested `RequiresPermission.Read`/`Write` as unsupported constructs.

- [ ] **Step 3: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_source_permission_scanner.py
```

Expected: import fails because the replacement scanner is absent.

- [ ] **Step 4: Implement bounded method lookup**

Use this query and reject methods without complete ranges:

```sql
SELECT node_id, line_start, line_end
FROM node
WHERE source_path = ?
  AND node_type IN ('JAVA_METHOD', 'KOTLIN_METHOD')
  AND line_start IS NOT NULL AND line_end IS NOT NULL
ORDER BY line_start, line_end, node_id
```

Select the owner with:

```python
min(candidates, key=lambda item: (item.line_end - item.line_start, item.node_id))
```

- [ ] **Step 5: Implement explicit semantic tables**

```python
ENFORCEMENT_APIS = {
    "enforceCallingPermission": "single",
    "enforceCallingOrSelfPermission": "single",
    "enforcePermission": "single",
    "enforceAnyPermissionOf": "any_of",
    "enforceAllPermissions": "all_of",
}
CHECK_APIS = frozenset({
    "checkCallingPermission", "checkCallingOrSelfPermission",
    "checkPermission", "checkSelfPermission",
})
```

For annotations, associate with the next method only when masked text between annotation and declaration contains annotations/modifiers/whitespace but no statement or type-body boundary. Store `api_name`, `argument_expression`, `resolution_kind`, `requirement_mode`, and `conditional` as applicable.

- [ ] **Step 6: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_source_permission_scanner.py \
  project/tests/unit/test_permission_lexical.py \
  project/tests/unit/test_permission_resolver.py \
  project/tests/unit/test_ctags_method_ranges.py
git add project/collectors/permission/source_permission_scanner.py \
  project/tests/unit/test_source_permission_scanner.py
git diff --cached --check
git commit -m "feat: classify method permission semantics"
```

Expected: all selected tests pass and checks/enforcement have different kinds.

---

### Task 6: Schedule permission semantics and capture source revisions

**Files:**
- Modify: `project/workspace/models.py`
- Modify: `project/workspace/config.py`
- Modify: `project/workspace/planner.py`
- Modify: `project/workspace/registry.py`
- Modify: `project/config/parser_registry.toml`
- Create: `project/workspace/revisions.py`
- Create: `project/tests/unit/test_permission_workspace.py`
- Modify: `project/tests/unit/test_workspace_v01.py`

**Interfaces:**
- Produces: `resolve_repository_revision(repository: Path) -> str | None`.
- Extends: `RepositorySpec.revision`, `WorkspacePlan.strict`, and `WorkspacePlan.strict_capability` serialization.
- Replaces `permission_declaration` and `permission_enforcement` with `permission_semantics`.

- [ ] **Step 1: Write planner/revision RED tests**

Create an enabled repository with Java, Kotlin, permission XML, and non-permission resource XML. Assert:

```python
tasks = {(task.language, task.capability): task for task in plan.tasks}
assert tasks[("xml", "permission_semantics")].status == "scheduled"
assert tasks[("java", "permission_semantics")].status == "scheduled"
assert tasks[("kotlin", "permission_semantics")].status == "scheduled"
assert ("xml", "symbols") not in tasks
assert plan.strict_capability == "permission_semantics"
```

Initialize a temporary Git repository with one commit and require its HEAD in `RepositorySpec.revision`; a non-Git extra repository returns `None` and serializes as `unknown` only in report output.

- [ ] **Step 2: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_workspace.py \
  project/tests/unit/test_workspace_v01.py
```

Expected: XML scheduling, strict intent, and revision assertions fail.

- [ ] **Step 3: Unify capabilities**

Add `xml` to `workspace/config.py:KNOWN`. Set planner/registry capabilities to:

```python
"java": ("symbols", "inheritance", "service_registration", "permission_semantics"),
"kotlin": ("symbols", "inheritance", "service_registration", "permission_semantics"),
"xml": ("permission_semantics",),
```

```toml
[parsers.xml]
implementation = "xml_permission_importer"
enabled = true
capabilities = ["permission_semantics"]
```

Remove both obsolete Permission capability names from canonical code/config/tests.

- [ ] **Step 4: Capture repository HEAD and strict intent**

```python
result = subprocess.run(
    ["git", "-C", str(repository), "rev-parse", "HEAD"],
    check=False, capture_output=True, text=True, timeout=10,
)
```

Accept only 40- or 64-character hexadecimal output. Store the revision on each enabled available repository. Serialize effective `strict` and `strict_capability` into `WorkspacePlan` so execution-stage parsers enforce the same request.

- [ ] **Step 5: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_workspace.py \
  project/tests/unit/test_workspace_v01.py
git add project/workspace/models.py project/workspace/config.py \
  project/workspace/planner.py project/workspace/registry.py \
  project/workspace/revisions.py project/config/parser_registry.toml \
  project/tests/unit/test_permission_workspace.py \
  project/tests/unit/test_workspace_v01.py
git diff --cached --check
git commit -m "feat: plan permission semantics coverage"
```

Expected: capability, strict-intent, and revision tests pass.

---

### Task 7: Materialize facts and write the structured report

**Files:**
- Create: `project/collectors/permission/materializer.py`
- Modify: `project/collectors/permission/report.py`
- Rewrite: `project/workspace/multi_permission.py`
- Create: `project/tests/unit/test_permission_materializer.py`
- Create: `project/tests/integration/test_permission_pipeline.py`
- Delete: `project/collectors/permission/java_permission_scanner.py`

**Interfaces:**
- Produces: `materialize_permission_facts(writer: GraphWriter, facts: Iterable[PermissionFact]) -> MaterializationSummary`.
- CLI remains `python -m workspace.multi_permission --plan PATH --db PATH --report PATH`.

- [ ] **Step 1: Write materializer RED tests**

Use an in-memory schema and assert exact directions:

```text
FILE -> DECLARES_PERMISSION -> PERMISSION
ANDROID_PACKAGE -> REQUESTS_PERMISSION -> PERMISSION
ANDROID_PACKAGE -> ALLOWLISTS_PRIVILEGED_PERMISSION -> PERMISSION
ANDROID_PACKAGE -> DENIES_PRIVILEGED_PERMISSION -> PERMISSION
ANDROID_PACKAGE -> DEFAULT_GRANTS_PERMISSION -> PERMISSION
METHOD -> REQUIRES_PERMISSION -> PERMISSION
METHOD -> CHECKS_PERMISSION -> PERMISSION
METHOD -> ENFORCES_PERMISSION -> PERMISSION
```

Require XML `FILE`/`ANDROID_PACKAGE` creation, existing method owners, preserved edge properties, and diagnostics instead of dangling edges.

- [ ] **Step 2: Write two-repository pipeline RED tests**

Create manifest, privapp, default-policy, Java, and Kotlin fixtures; seed bounded method nodes; run the CLI; assert:

```python
assert report["facts_and_edges_by_type"]["REQUESTS_PERMISSION"] >= 1
assert report["facts_and_edges_by_type"]["CHECKS_PERMISSION"] == 1
assert report["facts_and_edges_by_type"]["ENFORCES_PERMISSION"] == 1
assert report["source_revisions"]["frameworks/base"] == revision
assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
```

Run identical fixtures into fresh databases in reversed filesystem creation order and compare reports/semantic projections.

- [ ] **Step 3: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_materializer.py \
  project/tests/integration/test_permission_pipeline.py
```

Expected: materializer and new orchestrator imports fail.

- [ ] **Step 4: Implement deterministic materialization**

Create stable nodes with:

```python
stable_id("PERMISSION", fact.permission_name)
stable_id("ANDROID_PACKAGE", fact.package_name)
stable_id("FILE", fact.evidence.source_path)
```

Aggregate declarations before writes. Put a declaration property on `PERMISSION` only when every non-null value agrees; otherwise emit `declaration_conflicts` and retain every source edge. Sort facts by identity.

- [ ] **Step 5: Implement scheduled orchestration**

For every scheduled repository/language task: honor include/exclude rules; prefilter XML
candidates; pass the planned repository revision to every parser; load bounded methods
for Java/Kotlin; merge outcomes; materialize only after parsing; write the report using a
sibling temporary file and `Path.replace()`.

Keep dispatch explicit so unsupported languages cannot fall through to a Java parser:

```python
PARSERS = {
    "xml": scan_xml_repository,
    "java": scan_source_repository,
    "kotlin": scan_source_repository,
}

for task in permission_tasks(plan):
    parser = PARSERS.get(task["language"])
    if parser is None:
        report.add_task_failure(task, "missing_scheduled_parser")
        continue
    report.add_outcome(parser(task, plan=plan, database=args.db))

facts = report.sorted_facts()
materialize_permission_facts(writer, facts)
atomic_write_json(args.report, report.to_dict())
```

At the task boundary, unexpected exceptions become `task_failures`, the report is written,
and the CLI returns `1`. Under effective strict mode, a malformed candidate, an
undetermined dialect for a selected candidate, a missing scheduled parser, or a required
report/validation failure returns `2`. Unresolved expressions and declaration conflicts
remain non-fatal and return `0` after a completed task.

Every materialized edge sets `Edge.source_revision` and includes stable `fact_identity`,
repository, source revision, dialect, source expression, parser, and parser version
properties so the database column, graph evidence, and structured report agree.

- [ ] **Step 6: Remove obsolete production APIs**

```bash
rg -n "java_permission_scanner|scan_file_for_permissions|load_methods" project
```

Expected after deletion: no production matches. Historical documents may retain old names.

- [ ] **Step 7: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/unit/test_permission_materializer.py \
  project/tests/integration/test_permission_pipeline.py
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q project/tests
git add project/collectors/permission project/workspace/multi_permission.py \
  project/tests/unit/test_permission_materializer.py \
  project/tests/integration/test_permission_pipeline.py
git diff --cached --check
git commit -m "feat: materialize permission semantics graph"
```

Expected: deterministic report, graph semantics, FK checks, and full project suite pass.

---

### Task 8: Gate atomic publication on Permission validation

**Files:**
- Modify: `project/workspace/build_publish.py`
- Create: `project/workspace/permission_validation.py`
- Modify: `project/scripts/rebuild_all.sh`
- Create: `project/queries/permission_semantics_summary.sql`
- Create: `project/queries/package_permission_semantics.sql`
- Create: `project/tests/integration/test_permission_atomic_rebuild.py`
- Modify: `project/tests/integration/test_atomic_rebuild.py`
- Modify: `project/tests/unit/test_build_publish.py`

**Interfaces:**
- Produces: `validate_permission_report`, `validate_permission_database`, and `permission_semantic_fingerprint`.
- CLI: `python -m workspace.permission_validation --db PATH --report PATH [--require-aosp-evidence] [--fingerprint|--fingerprint-only]`.

- [ ] **Step 1: Write validation and atomic RED tests**

Reject missing report keys, FK errors, wrong endpoint node types, and duplicate active semantic edges. Require identical fingerprints for different insertion orders. In atomic fixtures, make strict Permission parsing fail; assert pre-existing live DB/report hashes remain unchanged and `--keep-failed-db` retains the staged report.

- [ ] **Step 2: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/integration/test_permission_atomic_rebuild.py \
  project/tests/integration/test_atomic_rebuild.py \
  project/tests/unit/test_build_publish.py
```

Expected: validation command/report directory assertions fail.

- [ ] **Step 3: Add the staging report directory**

```python
RAW_REPORT_DIRECTORIES = (
    "ctags", "aidl", "inheritance", "service", "permission",
)
```

Update build-publish tests to require `batch.raw / "permission"`.

- [ ] **Step 4: Implement validator and fingerprint**

Validate all design-required report keys, endpoint types for every Permission edge, duplicate tuples `(edge_type, from_node_id, to_node_id, source_path, line_start, properties_json)`, and `PRAGMA foreign_key_check`. Fingerprint canonical sorted Permission node/edge projections excluding timestamps/build IDs.

Use one authoritative endpoint table:

```python
EDGE_ENDPOINT_TYPES = {
    "DECLARES_PERMISSION": ({"FILE"}, {"PERMISSION"}),
    "REQUESTS_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "ALLOWLISTS_PRIVILEGED_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "DENIES_PRIVILEGED_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "DEFAULT_GRANTS_PERMISSION": ({"ANDROID_PACKAGE"}, {"PERMISSION"}),
    "REQUIRES_PERMISSION": ({"JAVA_METHOD", "KOTLIN_METHOD"}, {"PERMISSION"}),
    "CHECKS_PERMISSION": ({"JAVA_METHOD", "KOTLIN_METHOD"}, {"PERMISSION"}),
    "ENFORCES_PERMISSION": ({"JAVA_METHOD", "KOTLIN_METHOD"}, {"PERMISSION"}),
}
```

With `--require-aosp-evidence`, require `android.permission.MANAGE_USB`, at least one request, allow-or-deny, default grant with `fixed`/`whitelisted`, check, and enforce fact.

- [ ] **Step 5: Integrate before prepare/publish**

Use report path:

```text
$STAGED_RAW/permission/permission-semantics-report.json
```

Immediately after `workspace.multi_permission` and before `workspace.build_publish prepare`, run:

```bash
python -m workspace.permission_validation \
  --db "$STAGED_DB" \
  --report "$STAGED_RAW/permission/permission-semantics-report.json"
```

- [ ] **Step 6: Add queries**

`permission_semantics_summary.sql` groups active nodes/edges by type. `package_permission_semantics.sql` documents `.parameter set :package_name` and returns request, allow, deny, and default-grant edges for that package.

The summary query is based on the exact semantic edge allowlist:

```sql
SELECT edge_type, COUNT(*) AS edge_count
FROM edge
WHERE status = 'active'
  AND edge_type IN (
    'DECLARES_PERMISSION', 'REQUESTS_PERMISSION',
    'ALLOWLISTS_PRIVILEGED_PERMISSION', 'DENIES_PRIVILEGED_PERMISSION',
    'DEFAULT_GRANTS_PERMISSION', 'REQUIRES_PERMISSION',
    'CHECKS_PERMISSION', 'ENFORCES_PERMISSION'
  )
GROUP BY edge_type
ORDER BY edge_type;
```

- [ ] **Step 7: Verify GREEN and commit**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  project/tests/integration/test_permission_atomic_rebuild.py \
  project/tests/integration/test_atomic_rebuild.py \
  project/tests/unit/test_build_publish.py \
  project/tests/integration/test_permission_pipeline.py
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q project/tests
git add project/workspace/build_publish.py \
  project/workspace/permission_validation.py project/scripts/rebuild_all.sh \
  project/queries project/tests/integration/test_permission_atomic_rebuild.py \
  project/tests/integration/test_atomic_rebuild.py \
  project/tests/unit/test_build_publish.py
git diff --cached --check
git commit -m "feat: validate permission graph publication"
```

Expected: strict failure preserves live artifacts; valid staging passes report, endpoint, duplicate, and FK gates.

---

### Task 9: Document and verify against the real AOSP checkout

**Files:**
- Modify: `README.md`
- Modify: `project/README.md`
- Modify: `project/INSTALLATION_MANIFEST.txt`
- Modify: `doc/README.md`
- Modify: `doc/plans/2026-07-17-permission-enforcement-graph-v01-plan.md`
- Create: `doc/reviews/2026-07-22-permission-semantics-graph-v01-acceptance.md`
- Modify: `tests/test_documentation_contract.py`
- Modify if payload assertions require: `tests/test_canonical_project.py`

**Interfaces:**
- Consumes: all prior tasks and `/home/ts/aosp`.
- Produces: documented commands, supersession marker, and evidence-based acceptance record.

- [ ] **Step 1: Write documentation RED tests**

Require both READMEs to document `permission_semantics`, all eight edge types, `permission-semantics-report.json`, allowlist-versus-grant, and check-versus-enforce. Require the old plan to contain `Status: Superseded` and links to the new design/current plan.

- [ ] **Step 2: Verify RED**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q \
  tests/test_documentation_contract.py tests/test_canonical_project.py
```

Expected: new documentation contract assertions fail.

- [ ] **Step 3: Update commands and historical status**

Document:

```bash
bash scripts/rebuild_all.sh --strict-capability permission_semantics
sqlite3 -header -column data/android_context.db \
  < queries/permission_semantics_summary.sql
python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --fingerprint
```

Add below the old plan title:

```markdown
> **Status: Superseded.** Do not execute this plan. It is replaced by
> [the approved design](../designs/2026-07-21-permission-semantics-graph-v01-design.md)
> and [the current plan](2026-07-22-permission-semantics-graph-v01-plan.md).
```

- [ ] **Step 4: Run repository verification**

```bash
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q project/tests
python3 -m compileall -q project
bash -n setup.sh installers/install_project.sh project/scripts/rebuild_all.sh
git diff --check
```

Expected: both suites, compileall, Bash syntax, and diff check pass.

- [ ] **Step 5: Perform a disposable real-AOSP installation/rebuild**

Use a new target; if it exists, stop and choose another suffix rather than deleting it:

```bash
cd /mnt/d/AndroidContextIntelligence/.worktrees/permission-semantics-graph-v01
AOSP_ROOT=/home/ts/aosp \
PROJECT_ROOT=/home/ts/android-context-intelligence-permission-v01-acceptance \
bash ./setup.sh --fresh --rebuild
```

Expected: installation, tests, full rebuild, Permission validation, FK validation, and atomic publication return zero.

- [ ] **Step 6: Query real evidence and determinism**

```bash
cd /home/ts/android-context-intelligence-permission-v01-acceptance
python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --require-aosp-evidence --fingerprint
sqlite3 -header -column data/android_context.db \
  < queries/permission_semantics_summary.sql
sqlite3 data/android_context.db 'PRAGMA foreign_key_check;'
first="$(python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --fingerprint-only)"
bash scripts/rebuild_all.sh
second="$(python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --fingerprint-only)"
test "$first" = "$second"
```

Expected: semantic validation passes, FK query returns no rows, and fingerprints match.

- [ ] **Step 7: Record acceptance and commit**

Record commands, revisions, semantic counts, unresolved counts, both fingerprints, and elapsed times in the acceptance review. Then run:

```bash
git add README.md project/README.md project/INSTALLATION_MANIFEST.txt \
  doc/README.md doc/plans/2026-07-17-permission-enforcement-graph-v01-plan.md \
  doc/reviews/2026-07-22-permission-semantics-graph-v01-acceptance.md \
  tests/test_documentation_contract.py tests/test_canonical_project.py
git diff --cached --check
git commit -m "docs: accept permission semantics graph"
```

---

## Completion audit

- [ ] Old generic XML and line-oriented scanner code is absent from production paths.
- [ ] Every design edge type has unit and integration evidence.
- [ ] XML tests prove declarations, requests, allow, deny, and default grants.
- [ ] Java/Kotlin tests prove multiline extraction, constants, bounded owners, and check/enforce separation.
- [ ] Planner reports XML/Java/Kotlin `permission_semantics` per enabled repository.
- [ ] Report contains source revisions and all required diagnostic categories.
- [ ] Strict parser failure cannot replace live DB/reports.
- [ ] Root/canonical tests, compileall, Bash syntax, diff, FK, and duplicate gates pass.
- [ ] A disposable real-AOSP rebuild satisfies all spot checks.
- [ ] Two identical real-AOSP builds have the same Permission semantic fingerprint.
- [ ] WSL deployment is generated from tracked `project/`; no WSL source is copied into Git.
