# Android Context Intelligence

Current deterministic graph layers:

1. Java Symbol Graph v0.2.2
2. AIDL/Binder Graph v0.1

Canonical rebuild:

```bash
cd "/home/ts/android-context-intelligence"
./scripts/rebuild_all.sh
```

Current database:

```text
/home/ts/android-context-intelligence/data/android_context.db
```

Next layer:

- Java Inheritance Graph v0.1
- Binder transitive implementation query v0.1.1

## Multi-Repository Source Configuration v0.1

Repository discovery, language inventory, parser coverage and graph execution are configured in `config/source_roots.toml`. Repo manifest projects are discovered but disabled by default; explicitly enable repositories to control scan size.

```bash
./scripts/rebuild_all.sh --discover-only
./scripts/rebuild_all.sh --plan-only
./scripts/rebuild_all.sh
./scripts/rebuild_all.sh --strict
./scripts/rebuild_all.sh --strict-capability permission_enforcement
```

Java Symbol, AIDL/Binder, Java Inheritance and Java Service Registration support multiple enabled repositories. Kotlin, C/C++, Rust and HIDL are inventoried and reported as unsupported until a capability-specific parser is registered.

## Atomic Database Rebuild v0.1

The canonical rebuild creates one verified batch under `data/staging/<build-id>`
and atomically replaces `data/android_context.db` only after all reports and
validation gates pass. A pre-commit failure preserves the previous live batch.

```bash
./scripts/rebuild_all.sh
./scripts/rebuild_all.sh --keep-failed-db

sqlite3 data/android_context.db \
  "SELECT qualified_name FROM node WHERE node_type='GRAPH_BUILD';"
jq -r '.build_id' data/workspace/build-manifest.json
```

Failed batches are deleted by default or retained under `data/staging` with
`--keep-failed-db`. Interrupted publication is recovered automatically on the
next invocation. Concurrent rebuild, discover-only, and plan-only operations
are rejected through `data/.rebuild.lock`.
