# Android Context Intelligence Canonical Project

本目录是 Git 仓库中唯一的可执行项目源码。它会由根目录的
`setup.sh` 安装到 WSL；已安装目录不是源码来源，也不应反向同步。

完整安装、环境要求和风险说明见 [仓库 README](../README.md)。

## 受管内容

```text
collectors/  config/  configs/  graph/  queries/
scripts/     storage/ tests/    workspace/
```

安装器还会在部署根目录生成 `.android-context-installation.json`，记录
来源 commit 和每个受管文件的 SHA-256。以下是运行状态，不属于 payload：

```text
.venv/  data/  caches  backups  vendor inputs  decompiler output
```

upgrade 保留 `.venv/`、`data/`、`config/source_roots.local.toml` 和
`configs/local.yaml`。

源码范围配置分为两层：

- `config/source_roots.default.toml`：受 Git 与安装 manifest 管理的 canonical 基线；
- `config/source_roots.local.toml`：本机覆盖，升级时保留且不纳入 payload hash；
- `config/source_roots.local.toml.example`：本机覆盖示例。

旧部署的 `config/source_roots.toml` 会在 upgrade staging 中校验并迁移为
`source_roots.local.toml`；迁移失败会在发布新目录前终止。

## 部署后的命令

```bash
cd /home/ts/android-context-intelligence

bash scripts/rebuild_all.sh --discover-only
bash scripts/rebuild_all.sh --plan-only
bash scripts/rebuild_all.sh
bash scripts/rebuild_all.sh --keep-failed-db
bash scripts/rebuild_all.sh --strict
bash scripts/rebuild_all.sh \
  --vendor-input /home/ts/vendor-input \
  --jadx-bin /home/ts/jadx-1.5.6/bin/jadx

python scripts/profile_service_registration.py \
  --plan data/workspace/execution-plan.json \
  --db data/android_context.db \
  --cache-dir .cache/service-registration-profile \
  --output data/workspace/service-registration-profile.json

python scripts/graph_fingerprint.py \
  --db data/android_context.db
```

原子重建先写入 `data/staging/<build-id>`，通过外键、服务链和报告验证后
再发布 `data/android_context.db`。中断恢复和并发排斥由
`workspace.build_publish` 与 `data/.rebuild.lock` 管理。

Service profiler 只操作隔离数据库副本：先移除副本中的旧 Service 图，再执行
冷/热缓存导入并比较图指纹、AMS/PMS/LocalServices 计数、解析诊断和阶段耗时。

`graph_fingerprint.py` 对除 `GRAPH_BUILD` 和 `updated_at` 外的稳定图语义计算
SHA-256，用于比较两次独立全量构建；它不替代各图层的专用验证器。

## 开发验证

从 Git 仓库根目录运行：

```bash
python -m pytest -q project/tests
python -m compileall -q project
bash -n project/scripts/rebuild_all.sh
```

当前源码已将 Permission Semantics Graph、跨仓库定义冲突门禁、可复现
source/tool provenance 和 Vendor artifact staged 导入接入原子重建。

Vendor APK/JAR 必须放在 `data/` 外。反编译缓存按 artifact SHA-256、JADX
身份和选项寻址；Vendor 定义通过 `DERIVED_FROM_ARTIFACT` 回溯清单。JADX
部分输出标为 `degraded`，无可用源码则构建失败，live DB 不变。

## Permission Semantics Graph v0.1

执行计划能力名为 `permission_semantics`，产物报告为
`data/raw/permission/permission-semantics-report.json`。正式语义边为：

```text
DECLARES_PERMISSION
REQUESTS_PERMISSION
ALLOWLISTS_PRIVILEGED_PERMISSION
DENIES_PRIVILEGED_PERMISSION
DEFAULT_GRANTS_PERMISSION
REQUIRES_PERMISSION
CHECKS_PERMISSION
ENFORCES_PERMISSION
```

Allowlist is policy eligibility, not a runtime grant.
Check observes or returns; enforce denies by raising an error.

```bash
bash scripts/rebuild_all.sh --strict-capability permission_semantics
sqlite3 -header -column data/android_context.db \
  < queries/permission_semantics_summary.sql
python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --fingerprint
```

验证器在原子发布前检查报告字段、八类边端点、重复 active 边和 SQLite 外键。

升级安装会保留本地 `config/source_roots.local.toml`。canonical 默认配置已包含
`frameworks/base/data`，本地 include 与 canonical 必需根按稳定并集合并，因此
platform privapp 策略不会因旧本地覆盖而从扫描范围消失。

## Repository-scoped symbol definitions

跨仓库同名符号使用双层身份：`SYMBOL_DEFINITION` 保存每个仓库和源码位置的定义，
`DEFINES_SYMBOL` 指向稳定 logical symbol。`workspace.symbol_collision_validation`
在发布前输出 `data/workspace/symbol-collisions.json`；strict 构建遇到参与语义边的
歧义 logical symbol 会失败。

使用 `queries/symbol_definitions.sql` 查看所有仓库定义。需要源码定位时应选择
definition，语义边继续连接 logical symbol；不要把 logical node 的单一
`source_path` 当作唯一来源。

## Java/Kotlin call/dataflow workflow

`codeql/` 是受管 payload，包含 version-locked `java-kotlin` query pack。先用
`scripts/prepare_codeql.sh` 针对真实 AOSP 构建 `services` 与 `SystemUI`，再执行：

```bash
bash scripts/rebuild_all.sh \
  --codeql-db /path/to/verified/database \
  --strict-capability interprocedural_dataflow \
  --corrections-dir config/corrections \
  --retain-history
```

能力名为 `call_graph` 和 `interprocedural_dataflow`。调用事实使用 `MUST_CALL`、
`MAY_CALL` 与 `UNRESOLVED_CALL`；数据流、guard 和 Binder identity 分表保存，仅在
`SECURITY_TRACE` 中组合。Git 管理的 `FACT_CORRECTION` 支持 suppress/replace/
annotate/add，且不会改写提取器原始证据。

运维命令：

```bash
python scripts/graph_fingerprint.py --db data/android_context.db --format json
python scripts/graph_diff.py --before before.db --after after.db --format text
python -m workspace.call_dataflow_validation \
  --db data/android_context.db \
  --report data/workspace/call-dataflow-validation.json
```

无 `--codeql-db` 的默认构建只保证基础图，相关能力状态为 degraded；strict 模式
要求 verified DB、唯一身份 reconciliation、强证据与无 stale correction。
