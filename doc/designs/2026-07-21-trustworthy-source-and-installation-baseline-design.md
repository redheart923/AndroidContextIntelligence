# Trustworthy Source and Installation Baseline Design

## Status

Approved for implementation on 2026-07-21.

## Goal

Restore a trustworthy development and release baseline for Android Context
Intelligence before adding more graph layers. The Git repository must contain
the readable canonical project source, installation must be deterministic, and
the WSL project under `~/android-context-intelligence` must be a disposable
generated deployment rather than a second source of truth.

The clean-install distribution may include `setup.sh`, `installers/`, and a
canonical source or payload directory. There is no requirement to embed all
Python, SQL, TOML, and test files into shell heredocs or base64 blocks.

## Evidence from the repository review

The review used the Windows Git repository, the deployed WSL installer copy,
the generated WSL project, the live SQLite database, and the A17 vendor input
directory as current-state evidence.

### Release baseline failures

- The root test suite has four failures because
  `tests/test_installer_payload_sync.py` still references an installer that was
  moved from the repository root and a snapshot that is no longer tracked.
- The generated WSL project has one failing atomic rebuild integration test:
  53 tests pass and one fails because the Permission pipeline was added without
  updating the integration fixture contract.
- `.gitignore` contains a UTF-16/NUL encoded suffix. Consequently,
  `android-context-current/` is not reliably ignored and pollutes recursive
  pytest discovery.
- A stale linked worktree remains under `.worktrees/` from an incomplete WSL
  snapshot synchronization attempt.
- `main` is ahead of `origin/main`; this is operationally relevant but remote
  publication is outside this design's implementation scope.

### Source-of-truth and installer failures

- The self-extracting installers are the only tracked implementation source.
  Important Python modules are stored inside very large heredocs or base64
  payloads and are difficult to review or test directly.
- Multiple stages replace complete shared files. In particular,
  `scripts/rebuild_all.sh` is written by stages 1, 4, and 6, while
  `workspace/languages.py` and `config/parser_registry.toml` are written by
  stages 4 and 6.
- The Stage 6 parser registry overwrites newer Kotlin parser capabilities from
  Stage 4. Correctness therefore depends on every later installer containing a
  byte-for-byte updated copy of every earlier shared file.
- The generated WSL project and the Windows installer payload have already
  diverged. Manual edits to the generated project cannot be treated as a
  release process.
- The Permission installer ignores the exported `PROJECT_ROOT` variable and
  derives its target from its first positional argument or `$HOME`. Custom
  project paths are therefore not consistently supported.
- Interactive `read -p` prompts at the end of `setup.sh` make unattended
  installation unreliable.

### Graph correctness and reproducibility failures

- The live database contains only 27 `REQUIRES_PERMISSION` and 8
  `ENFORCES_PERMISSION` edges. No XML permission-declaration edges were built.
- The planner does not define XML's `permission_declaration` capability.
  Instead, 12,703 XML files are reported as `symbols: unsupported`, so the XML
  collector is never scheduled.
- Permission XML exceptions are swallowed, making missing facts invisible.
- Permission method association uses only `line_start`; it does not enforce a
  method end boundary and can attach a check to the wrong method.
- Common constant expressions such as `Manifest.permission.X` and multiline
  annotations/calls are not supported, explaining the very low coverage.
- Every one of the 574,399 live nodes has `source_revision = unknown`.
  The build manifest contains timestamps and a config path but no repository
  revisions, so a graph build is not reproducible.
- README completion claims for Permission and Vendor graphs do not match the
  live database. The current database contains no evidence for the documented
  "Vendor 2M+" state.

### Vendor ingestion failures

- Vendor import modifies the live SQLite database directly instead of using
  the rebuild lock, staging batch, validation gates, and atomic publication.
- Vendor inputs are documented under `data/raw/vendor`, while `data/raw` is a
  published output replaced by canonical rebuilds. Inputs and generated output
  therefore have conflicting lifecycles.
- Decompilation output is reused solely because a directory exists; artifact
  hashes and JADX status are not recorded. Partial or stale decompilations can
  be imported without a reliable manifest.
- The configured JADX 1.5.6 executable and A17 artifacts exist in WSL, but the
  pipeline does not record their versions or SHA-256 identities in the graph
  build manifest.

## Considered approaches

### A. Keep multi-stage self-extracting installers

Add more payload-sync tests and continue maintaining heredoc/base64 copies.
This minimizes immediate file movement but preserves the full-file overwrite
coupling that caused the current drift. Rejected.

### B. Track canonical source and install from it

Track the complete generated-project source in a dedicated canonical directory.
Use a thin installer to copy and validate that tree. This is readable,
testable, deterministic, and compatible with the approved distribution model.
Selected.

### C. Publish only a prebuilt tar archive

A deterministic archive would install cleanly, but using an archive as the
only reviewable payload merely moves the opaque-source problem. A generated
archive can be added later as a release artifact, but it must be built from the
tracked canonical tree. Deferred.

## Selected architecture

### Repository layout

```text
AndroidContextIntelligence/
├── project/                         # canonical generated-project source
│   ├── collectors/
│   ├── config/
│   ├── graph/
│   ├── queries/
│   ├── scripts/
│   ├── storage/
│   ├── tests/
│   ├── workspace/
│   ├── README.md
│   ├── INSTALLATION_MANIFEST.txt
│   └── requirements-lock.txt
├── installers/
│   ├── install_project.sh           # canonical thin installer
│   └── legacy/ or compatibility wrappers
├── scripts/
│   ├── verify_project_install.py
│   └── optional release-packaging tools
├── tests/                            # repository/release contract tests
├── doc/
└── setup.sh
```

`project/` is the only implementation source of truth. Installer files must
not contain independently maintained copies of project modules.

### Installation flow

1. `setup.sh` validates required tools and resolves `AOSP_ROOT`,
   `PROJECT_ROOT`, and the canonical payload root.
2. `installers/install_project.sh` creates a staging installation directory
   next to the target.
3. The installer copies the tracked `project/` tree with an explicit allowlist.
   Runtime-only paths such as `.venv`, `data`, caches, and local configuration
   are not part of the payload.
4. It writes installation metadata containing the source Git commit and a
   manifest of payload file hashes.
5. It runs syntax checks and the project test suite in the staged target.
6. On success, it atomically promotes the staged target for `--fresh`, or
   updates the source portion while preserving declared runtime paths for an
   upgrade.
7. The canonical graph rebuild is an explicit setup option. Installation and a
   multi-hour AOSP rebuild are separate operations.

### Generated WSL project policy

- `~/android-context-intelligence` is disposable and must be reproducible from
  the Windows Git checkout.
- It is not synchronized back to Windows during normal development.
- Direct source edits in that directory are prohibited. A drift-check command
  compares it with the installed payload manifest and reports differences.
- Runtime paths such as `.venv`, `data`, caches, logs, and vendor staging remain
  local to WSL.
- `~/android-context-installers` may be a deployment copy, but the Windows Git
  repository remains authoritative.

### Command contract

The top-level entry point will support non-interactive operation:

```bash
./setup.sh --fresh
./setup.sh --upgrade
./setup.sh --verify-only
./setup.sh --fresh --rebuild
./setup.sh --upgrade --rebuild
```

Vendor import is never prompted interactively. It is requested explicitly in a
later milestone through an option such as `--vendor-input DIR` or by running a
dedicated command.

All path-bearing commands honor environment variables consistently:

```bash
AOSP_ROOT=/path/to/aosp \
PROJECT_ROOT=/path/to/android-context-intelligence \
./setup.sh --fresh
```

### Git and release policy

- Every source or documentation modification is committed on a feature branch.
- Generated runtime data is ignored and never committed.
- Installer verification compares installed files against the canonical
  `project/` tree and its hash manifest, not against a manually synchronized
  WSL snapshot.
- Compatibility wrappers may remain for one release but must delegate to the
  canonical installer; they may not overwrite project files independently.
- The old untracked `android-context-current/` directory is not migrated as-is.
  Its contents are evidence for drift analysis only.

## Milestone 1 scope

Milestone 1 restores the trustworthy baseline only. It does not attempt to fix
Permission extraction semantics, Vendor atomic ingestion, provenance, or
incremental updates in the same change.

### Included

- Repair `.gitignore` as UTF-8 text and cover generated/deployment paths.
- Introduce the tracked canonical `project/` source tree.
- Select source files by comparing current installers and the deployed WSL
  project; every difference must be resolved intentionally.
- Add a thin canonical installer and update `setup.sh` to delegate to it.
- Eliminate shared-file replacement by stages 1/4/6 from the canonical path.
- Make setup non-interactive and consistently honor `PROJECT_ROOT`.
- Add repository tests for payload completeness, byte equality, executable
  modes where relevant, and forbidden generated files.
- Repair the atomic integration fixture so the canonical project suite passes.
- Update documentation to state the source-of-truth and synchronization rules.
- Keep existing graph functionality behavior-compatible; semantic permission
  improvements belong to Milestone 2.

### Excluded

- New graph node or edge types.
- Permission constant resolution and XML grant modeling.
- Atomic Vendor graph publication.
- Repository revision collection.
- Build Graph or incremental graph updates.

## Error handling

- Installation fails before target promotion if a payload file is missing, a
  source hash differs, syntax validation fails, or tests fail.
- A failed fresh installation leaves the existing target unchanged.
- An upgrade creates a timestamped rollback copy of the source/configuration
  portion and reports its location.
- Unknown command-line options fail with usage text.
- Non-interactive execution never waits for input.
- The drift verifier returns a nonzero exit code and lists added, removed, and
  modified deployed source files.

## Testing strategy

### Repository tests

- Canonical project manifest covers every tracked payload file.
- No cache, database, raw report, vendor input, or virtual environment is part
  of the payload.
- Installer dry-run and path-resolution tests cover default and custom roots.
- Compatibility wrappers delegate without independently writing shared files.
- The root test suite does not discover tests from untracked runtime snapshots.

### Canonical project tests

- All current unit and integration tests run from `project/`.
- Atomic rebuild fixtures include every invoked pipeline module and do not parse
  CLI arguments at import time.
- Shell scripts pass `bash -n`.
- Python modules pass compilation and pytest.

### WSL acceptance

- Install into a temporary target from the Windows checkout.
- Verify the installed payload hash manifest.
- Run the installed project tests.
- Verify custom `PROJECT_ROOT` behavior.
- Verify an unattended invocation does not read stdin.
- Do not run the full five-hour AOSP rebuild as part of every packaging test;
  retain a separate explicit real-AOSP acceptance gate.

## Acceptance criteria

Milestone 1 is complete only when:

1. `project/` is tracked and is the documented implementation authority.
2. Root and canonical project tests pass in WSL.
3. All shipped shell scripts pass `bash -n`.
4. A temporary clean installation reproduces the canonical tree byte-for-byte.
5. The generated WSL target is not required as an input to any test or build.
6. `PROJECT_ROOT` works for every install step.
7. Setup has no unconditional interactive prompts.
8. Existing runtime data and the live WSL project remain untouched by the
   temporary acceptance test.
9. Every modification is committed on the feature branch.

## Follow-on milestones

After Milestone 1:

1. Correct Permission Graph scheduling and semantics.
2. Move Vendor inputs outside published outputs and make Vendor ingestion
   atomic and reproducible.
3. Record repository revisions and dirty state in the build manifest and graph
   provenance.
4. Add Build Graph foundations.
5. Add fingerprinted incremental updates, then Runtime/Test Graph ingestion.
