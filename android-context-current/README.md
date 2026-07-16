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

## Java Inheritance Graph v0.1

Edges:

- `EXTENDS`
- `IMPLEMENTS_JAVA_INTERFACE`

Package Manager transitive Binder query:

```bash
sqlite3 -header -column data/android_context.db   < queries/package_manager_transitive_binder.sql
```

## System Service Registration Graph v0.1

Covered APIs:

- `ServiceManager.addService()`
- `publishBinderService()`
- `LocalServices.addService()`

Nodes:

- `SERVICE_REGISTRATION`
- `BINDER_SERVICE_NAME`
- `LOCAL_SERVICE_KEY`

Edges:

- `REGISTERS_BINDER_NAME`
- `REGISTERS_LOCAL_KEY`
- `REGISTERS_INSTANCE`
- `REGISTERED_AS`
- `EXPOSED_AS_LOCAL_SERVICE`

Queries:

```bash
sqlite3 -header -column data/android_context.db   < queries/ams_service_chain.sql

sqlite3 -header -column data/android_context.db   < queries/pms_service_chain.sql

sqlite3 -header -column data/android_context.db   < queries/local_services_summary.sql
```
