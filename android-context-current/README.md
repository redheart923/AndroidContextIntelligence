# Android Context Intelligence

This snapshot is the tested source for the self-contained WSL installers. It
contains the deterministic Java Symbol, AIDL/Binder, Java Inheritance, System
Service Registration, and Multi-Repository graph layers.

## Canonical rebuild

```bash
cd /home/ts/android-context-intelligence
./scripts/rebuild_all.sh
```

The rebuild creates a complete batch under `data/staging/<build-id>`. The batch
contains the SQLite database, workspace reports, raw reports, and
`workspace/build-manifest.json`. Reports are published first and the prepared
single-file SQLite database is atomically replaced last. A pre-commit failure
therefore leaves the last verified live database and reports unchanged.

Retain a failed batch for diagnosis:

```bash
./scripts/rebuild_all.sh --keep-failed-db
```

The retained path is printed and remains under `data/staging/<build-id>`.
Without this option, failed staging is deleted automatically. Interrupted
publication is recovered at the start of every canonical rebuild, or manually:

```bash
python -m workspace.build_publish recover --data-root data
```

All rebuild, discover-only, and plan-only invocations share
`data/.rebuild.lock`. A concurrent invocation exits with
`another rebuild is already running` before changing reports.

## Verify the published batch

```bash
sqlite3 data/android_context.db \
  "SELECT qualified_name FROM node WHERE node_type='GRAPH_BUILD';"

jq -r '.build_id' data/workspace/build-manifest.json

sqlite3 data/android_context.db 'PRAGMA foreign_key_check;'
```

The two build IDs must match, the foreign-key query must produce no output, and
`data/android_context.db-wal` / `data/android_context.db-shm` must be absent.

## Installation boundary

A clean AOSP checkout does not need this snapshot copied into WSL. The five
root shell scripts are the installation inputs; the recommended entry point is:

```bash
./setup_android_context_intelligence_complete_v01.sh --fresh
```

`android-context-current` is retained as the version-controlled payload and
test baseline used to verify those installers.
