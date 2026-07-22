# Permission Semantics Graph v0.1 Design

## Status

Approved for planning on 2026-07-21.

This design supersedes the extraction model in
`doc/plans/2026-07-17-permission-enforcement-graph-v01-plan.md`. That plan must
not be executed as written because it treats unrelated XML dialects as one
schema, scans source one line at a time, and cannot associate evidence with a
bounded method range.

## Goal

Build a complete, auditable Permission Semantics Graph for the enabled AOSP
repositories. Version 0.1 covers all three approved source domains:

1. permission declarations and package requests in `AndroidManifest.xml`;
2. privileged and default-permission policy, including explicit deny rules;
3. Java and Kotlin permission contracts, checks, and throwing enforcement.

The graph must distinguish policy intent from runtime enforcement. It must not
describe a privapp allowlist entry as a completed runtime grant, and it must
not describe a non-throwing permission check as enforcement.

## Current-state evidence

The accepted trustworthy-source baseline established the following facts:

- the live graph has only 27 `REQUIRES_PERMISSION` and 8
  `ENFORCES_PERMISSION` edges;
- its 20 permission nodes all originate from the Java permission scanner;
- no XML permission declaration, package request, privapp policy, or default
  permission relationship is present;
- 12,703 XML files are currently reported as unsupported symbol input rather
  than scheduled for permission semantics;
- the Java/Kotlin scanner is line-oriented, literal-only, and associates
  evidence using only `line_start`;
- the Ctags installation exposes the `end` field, but the Java symbol importer
  currently stores `line_end = line_start`.

These numbers are evidence of missing coverage, not acceptance targets for the
new implementation.

## Non-goals

Version 0.1 does not attempt to prove the final effective permission state of a
running Android device. It does not model:

- runtime grant and revoke operations in `packages.xml` or package-manager
  runtime state;
- AppOps mode, SELinux decisions, role assignment, user restrictions, or
  device-policy overrides;
- full Java/Kotlin AST, call graph, control flow, or interprocedural data flow;
- native C/C++/Rust permission enforcement;
- permission-to-Binder-method correspondence inferred only from matching names;
- build-variant selection beyond the repositories and files made available by
  the existing workspace execution plan.

Unsupported languages and unresolved expressions remain visible in structured
coverage reports. They are never silently treated as successfully parsed.

## Semantic model

### Nodes

#### `PERMISSION`

Stable identity:

```text
PERMISSION:<fully-qualified-permission-name>
```

Examples:

```text
PERMISSION:android.permission.MANAGE_USB
PERMISSION:com.example.permission.INTERNAL_CONTROL
```

Declaration attributes are stored on the node only when they are consistent
across observed declarations. Relevant properties include:

```text
name
protection_level
permission_group
label_expression
description_expression
known_signer_expression
```

Every declaration remains a separate source fact, so node properties do not
replace provenance.

#### `ANDROID_PACKAGE`

Stable identity:

```text
ANDROID_PACKAGE:<manifest-package-name>
```

For policy files whose package is an XML key rather than a manifest owner, the
same node type is used because the key names the Android package receiving the
policy. The fact records its source dialect so callers can distinguish
manifest ownership from policy targeting.

#### Existing source nodes

The design reuses:

```text
FILE
JAVA_METHOD
KOTLIN_METHOD
```

No synthetic method node is created when source evidence cannot be associated
with an existing bounded method. Such evidence is retained as unresolved in
the report.

### Edges

| From | Edge | To | Meaning |
|---|---|---|---|
| `FILE` | `DECLARES_PERMISSION` | `PERMISSION` | The manifest declares a permission. |
| `ANDROID_PACKAGE` | `REQUESTS_PERMISSION` | `PERMISSION` | The package requests a permission in its manifest. |
| `ANDROID_PACKAGE` | `ALLOWLISTS_PRIVILEGED_PERMISSION` | `PERMISSION` | Privapp policy permits the package to receive the privileged permission if all other grant conditions hold. |
| `ANDROID_PACKAGE` | `DENIES_PRIVILEGED_PERMISSION` | `PERMISSION` | Privapp policy explicitly denies the permission. |
| `ANDROID_PACKAGE` | `DEFAULT_GRANTS_PERMISSION` | `PERMISSION` | Default-permissions policy directs an initial/default grant. |
| method | `REQUIRES_PERMISSION` | `PERMISSION` | An annotation publishes a caller contract. |
| method | `CHECKS_PERMISSION` | `PERMISSION` | Code observes permission state without guaranteed throwing enforcement. |
| method | `ENFORCES_PERMISSION` | `PERMISSION` | Code uses a recognized throwing enforcement API. |

All edges carry evidence properties sufficient to explain the fact:

```text
repository
source_path
line_start
line_end
source_dialect
source_expression
parser
parser_version
```

Dialect-specific properties are preserved rather than flattened away:

- `REQUESTS_PERMISSION`: `max_sdk_version`, `uses_permission_flags`;
- `DEFAULT_GRANTS_PERMISSION`: `fixed`, `whitelisted`;
- `REQUIRES_PERMISSION`: `annotation_form`, `requirement_mode`, `conditional`;
- method evidence: `api_name`, `argument_expression`, `resolution_kind`.

`requirement_mode` is one of `single`, `all_of`, or `any_of`. An `anyOf`
annotation creates one edge for each permission with the shared mode retained
on every edge; consumers must not interpret the edges as independent mandatory
requirements.

## XML extraction

### Candidate selection

The workspace planner adds XML support for the graph capability
`permission_semantics`. XML is not presented as having a general-purpose
symbol parser.

To avoid parsing every resource XML file as a permission document, a cheap
candidate stage selects files using path/name and root-element evidence. The
candidate stage is an optimization only: every selected file is parsed with a
context-specific parser, and candidate/parser counts are reported.

### `AndroidManifest.xml`

Supported elements:

```xml
<permission ... />
<uses-permission ... />
<uses-permission-sdk-23 ... />
<uses-permission-sdk-m ... />
```

The parser reads the manifest package name and Android-namespaced attributes.
`<permission>` produces a `DECLARES_PERMISSION` fact. Each
`<uses-permission*>` produces a `REQUESTS_PERMISSION` fact and retains
`maxSdkVersion` and `usesPermissionFlags` where present.

### Privapp permission policy

Supported structure:

```xml
<permissions>
  <privapp-permissions package="...">
    <permission name="..." />
    <deny-permission name="..." />
  </privapp-permissions>
</permissions>
```

`permission` creates `ALLOWLISTS_PRIVILEGED_PERMISSION` and
`deny-permission` creates `DENIES_PRIVILEGED_PERMISSION`. A deny rule is never
collapsed into a Boolean attribute on an allow edge.

### Default-permissions policy

Supported structure:

```xml
<exceptions>
  <exception package="...">
    <permission name="..." fixed="..." whitelisted="..." />
  </exception>
</exceptions>
```

Each permission creates `DEFAULT_GRANTS_PERMISSION` and preserves the `fixed`
and `whitelisted` values as normalized booleans when valid. Invalid values are
reported instead of silently coerced.

### XML correctness rules

- Parsing is selected by document dialect; the implementation must not use a
  generic `.//permission` rule across all XML files.
- Malformed candidates are reported with repository, path, dialect, and parse
  error.
- Unknown elements are ignored only after the document dialect is identified;
  they are counted for forward-compatibility reporting.
- Duplicate identical facts are deterministically deduplicated.
- Conflicting declarations of the same permission are retained as separate
  evidence and emitted in `declaration_conflicts`; there is no last-write-wins
  merge.
- Relative permission names are resolved using the declaring manifest package
  according to Android manifest naming rules.

## Java and Kotlin extraction

### Method ranges

The Ctags collection command requests the `end` field. Java and Kotlin symbol
importers store a real inclusive `line_end` when available. Universal Ctags
5.9.0 emits that field for Java methods but not for Kotlin methods, so the
symbol importer uses a layout-preserving balanced source-range fallback when
the field is absent. It never invents a range when no balanced method body can
be identified. A source fact is
attached to the smallest method range satisfying:

```text
line_start <= evidence_line <= line_end
```

Annotations immediately preceding a method are associated with that method
using the declaration boundary recorded by the source scanner. Evidence
outside a unique method range remains unresolved; it is not attached to the
nearest preceding method.

### Lexical scanner

Version 0.1 uses a lightweight balanced lexical scanner, not a full compiler
frontend. It must:

- skip line comments, block comments, string contents, and character contents
  when looking for call/annotation structure;
- preserve string literal values used as permission arguments;
- balance parentheses, braces, and brackets across lines;
- recognize qualified and statically imported API names;
- report constructs it cannot parse or resolve.

This is sufficient for multiline annotations and calls while keeping the
pipeline independent of a complete AOSP build.

### Permission expression resolution

The resolver supports, in order:

1. string literals;
2. `android.Manifest.permission.NAME`;
3. `Manifest.permission.NAME` when the import is unambiguous;
4. statically imported permission constants;
5. same-file `static final String` / Kotlin `const val` constants;
6. bounded same-file alias chains.

Alias traversal has a fixed maximum depth and cycle detection. Ambiguous,
computed, cross-file custom constants, and unsupported expressions are emitted
as unresolved facts with the original expression. No permission name is
guessed from the identifier alone.

### `@RequiresPermission`

Supported forms include:

```text
@RequiresPermission(PERMISSION)
@RequiresPermission(value = PERMISSION)
@RequiresPermission(allOf = {...})
@RequiresPermission(anyOf = {...})
@RequiresPermission(conditional = true, ...)
```

The scanner accepts recognized Android/AndroidX annotation imports and records
the exact annotation type. Nested `Read`/`Write` annotations are out of scope
for v0.1 and are reported as unsupported constructs.

### Check versus enforcement classification

The recognized API table is explicit and versioned. Examples of throwing
enforcement families include:

```text
enforceCallingPermission
enforceCallingOrSelfPermission
enforcePermission
enforceAnyPermissionOf
enforceAllPermissions
```

Recognized non-throwing observation families include:

```text
checkCallingPermission
checkCallingOrSelfPermission
checkPermission
checkSelfPermission
```

Non-throwing checks produce `CHECKS_PERMISSION`, never
`ENFORCES_PERMISSION`. Wrapper methods are classified only from direct evidence
inside the method in v0.1; the collector does not infer interprocedural
enforcement.

## Pipeline integration

### Capability planning

The parser registry and workspace plan use `permission_semantics` as the graph
capability:

```text
XML     -> manifest and policy semantics
Java    -> annotation, check, and enforcement semantics
Kotlin  -> annotation, check, and enforcement semantics
```

Language detection alone is not coverage. A scheduled parser must produce a
completion status for every assigned repository/language task.

### Atomic publication

Permission import runs inside the existing canonical staging-database rebuild.
It does not open or mutate the published live database independently. Parser,
validation, or strict-coverage failure prevents publication and leaves the
previous database intact.

### Determinism

- input repositories and revisions come from the workspace execution plan;
- facts are sorted by stable identity before writing reports;
- duplicate edges have a deterministic identity including semantic kind and
  source evidence;
- reports contain no volatile ordering;
- two builds from identical source revisions and configuration produce the
  same semantic node/edge set.

## Structured report

The canonical report path is:

```text
data/raw/permission/permission-semantics-report.json
```

It contains at least:

```text
schema_version
parser_version
source_revisions
repositories_scanned
files_scanned_by_language
xml_candidates_by_dialect
xml_documents_parsed_by_dialect
facts_and_edges_by_type
duplicate_facts
declaration_conflicts
malformed_xml
unresolved_permission_expressions
unresolved_method_owners
unsupported_constructs
task_failures
```

Every error or unresolved entry includes repository, source path, line range
where available, original expression, and reason code.

## Strict-mode contract

Default mode imports supported facts and publishes a visible coverage summary.
Unresolved user-code expressions may remain non-fatal if the parser task
completed successfully.

`--strict-capability permission_semantics` fails the rebuild when any of the
following occurs:

- a scheduled repository/language task has no runnable parser;
- a candidate XML document is malformed or its dialect cannot be determined;
- a permission parser raises an internal error;
- a required report or validation stage is missing.

Unresolved source expressions and conflicting permission declarations are data
quality findings, not parser task failures. They remain non-fatal under both
`--strict` and `--strict-capability permission_semantics` in v0.1, but their
counts and evidence must be present in the final coverage summary. A future
release may add an explicit configurable quality gate without changing the
meaning of strict parser coverage.

## Validation and acceptance

### Unit tests

Tests must cover:

- manifest declaration and all supported request element variants;
- request attributes (`maxSdkVersion`, `usesPermissionFlags`);
- privapp allow and deny as distinct semantics;
- default grants with valid and invalid `fixed`/`whitelisted` values;
- relative manifest permission names;
- malformed XML and conflicting declarations;
- multiline Java and Kotlin annotations/calls;
- comments and string contents that resemble APIs but are not evidence;
- literals, Manifest constants, static imports, same-file aliases, cycles, and
  ambiguous expressions;
- `value`, `allOf`, `anyOf`, and `conditional` annotation semantics;
- check versus enforce classification;
- smallest-containing-method association and unresolved owner behavior;
- real Ctags `line_end` import.

### Planner and integration tests

Tests must prove:

- XML is scheduled for `permission_semantics` without claiming general symbol
  coverage;
- Java and Kotlin permission tasks are scheduled for each enabled applicable
  repository;
- one repository's failure prevents staging publication;
- the prior live database remains unchanged after a failed rebuild;
- report schema and deterministic ordering remain stable;
- foreign-key and duplicate-edge checks pass.

### AOSP acceptance evidence

At minimum, the integration fixture or configured AOSP checkout must verify:

- declaration of `android.permission.MANAGE_USB`;
- at least one package `REQUESTS_PERMISSION` edge from a real
  `AndroidManifest.xml`;
- allow and/or deny policy from `privapp-permissions-platform.xml`;
- a default-permission edge with preserved `fixed` or `whitelisted` metadata;
- direct Java enforcement and a non-throwing check producing different edge
  types;
- no foreign-key violations;
- a second identical rebuild produces the same Permission semantic counts and
  identities.

Absolute edge counts are reported but are not hard-coded acceptance values,
because enabled repositories and AOSP revisions are configurable.

## Delivery shape

Implementation belongs in the tracked canonical `project/` tree. The thin
installer copies that source; no new self-extracting Permission installer or
independently maintained shell payload is introduced.

Expected implementation areas include:

```text
project/collectors/permission/
project/workspace/
project/config/parser_registry.toml
project/scripts/rebuild_all.sh
project/tests/
project/queries/
```

Each implementation slice is committed independently on the Permission feature
branch. The WSL deployment is regenerated from the tracked source and is never
used as an upstream source of code.

## Implementation-plan boundary

The implementation plan following this design must use test-driven slices and
must explicitly replace the obsolete 2026-07-17 plan. It should sequence work
so that schema/model tests, method-range correctness, XML dialects, source
semantics, planner integration, atomic rebuild behavior, and real-AOSP
acceptance can be verified independently.
