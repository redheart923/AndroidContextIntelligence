# Canonical Project Source Drift Audit

## Scope

This audit records the one-time migration used to create the tracked
`project/` source tree. The migration source was the deployed WSL project at
`/home/ts/android-context-intelligence` on 2026-07-21. That directory remains
a generated deployment and is not a normal build or test input after this
migration.

Only these entries were eligible for migration:

```text
.gitignore
INSTALLATION_MANIFEST.txt
README.md
requirements-lock.txt
collectors/
config/
configs/
graph/
queries/
scripts/
storage/
tests/
workspace/
```

The migration explicitly excluded `.git`, `.venv`, `venv`, `data`,
`backups`, `.pytest_cache`, `__pycache__`, `*.pyc`, and installer backup files.
The copied tree contained 29 collector files, 2 config files, 1 local-config
template, 4 graph files, 24 queries, 2 scripts, 1 storage file, 24 tests, and
26 workspace files before runtime caches were created by verification.

## Comparison with tracked installer payloads

The effective deployment was compared with every overlapping payload that can
be extracted from the tracked installers.

### Multi-repository installer

| Path | Result | Decision |
|---|---|---|
| `workspace/build_publish.py` | Byte-identical | Use deployed/canonical file. |
| `tests/unit/test_build_publish.py` | Byte-identical | Use deployed/canonical file. |
| `tests/integration/test_atomic_rebuild.py` | Byte-identical | Use deployed file, then repair its known missing Permission fixture under TDD. |
| `scripts/rebuild_all.sh` | Different | The multi-repository copy predates the Permission stage. Use the final deployed file, which is byte-identical to the Permission installer payload. |

The differing `rebuild_all.sh` hashes were:

```text
multi-repository payload: f52bda924cbd...
final deployed project:  0c17413d05b3...
```

### Permission installer

All seven embedded Permission-stage payloads were byte-identical to the final
deployed project:

```text
workspace/languages.py
config/parser_registry.toml
workspace/multi_permission.py
collectors/permission/__init__.py
collectors/permission/xml_permission_importer.py
collectors/permission/java_permission_scanner.py
scripts/rebuild_all.sh
```

This confirms the overwrite coupling identified in the architecture review:
the final project is only correct because the later Permission stage replaces
the earlier multi-repository `rebuild_all.sh` and parser registry. The new
canonical source tree records that final state directly; future installers
must copy it rather than reconstructing it from stage order.

## Verification result at migration

- Canonical boundary tests: 4 passed.
- Canonical project suite: 53 passed, 1 failed.
- The single failure is the pre-existing
  `test_successful_publication_exposes_matching_build_ids` fixture defect: the
  fixture imports the real `workspace.multi_permission` CLI and triggers
  argparse because it does not provide the same report stub as the other
  pipeline modules.
- No additional test failures were introduced by the migration.

That fixture is intentionally repaired later in the baseline plan after the
installer and payload contracts have been established.
