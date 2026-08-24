# Partial Source Workspace Profile v0.1 Design

Status: `APPROVED FOR PLANNING / NOT IMPLEMENTED`

Recorded: 2026-08-24

## 1. Goal

让 AndroidContextIntelligence 在只有一个或多个局部源码仓库时，仍能生成、验证并原子发布一个可信的局部图谱，同时保证该产物不会被解释为完整 AOSP 图谱。

局部源码可以是：

- AOSP 根目录下已同步的少量 Repo 项目；
- 保持 AOSP 相对路径的源码目录；
- 通过 `extra_repositories` 配置的任意本地 Git 仓库；
- Java、Kotlin、AIDL、XML 等当前已有解析器支持的混合仓库。

本阶段不要求完整 AOSP 构建环境，也不以缺失的跨仓库关系冒充已解析事实。

## 2. Current Evidence and Problem

2026-08-24 对 WSL 现场状态的只读审计显示：

```text
/home/ts/aosp repositories:
  frameworks/base
  frameworks/libs/systemui
  frameworks/native

/home/ts/aosp/build/envsetup.sh: missing
deployed source_roots.local.toml: only aosp_root override
live android_context.db: 3,571,826,688 bytes
live enabled inventory: platform/frameworks/base
```

现有 planner 和 collectors 已经支持多仓库和局部目录，但 canonical rebuild 仍有以下完整 Framework 假设：

1. `rebuild_all.sh` 无条件要求至少一条 `EXPOSED_AS_LOCAL_SERVICE`；普通模块即使成功生成符号图，也无法发布。
2. 显式启用但不存在的仓库，在 non-strict planner 中只形成 gap，可能继续进入昂贵构建阶段。
3. `GRAPH_BUILD`、build manifest 和查询结果没有一等的 `analysis_scope`，无法稳定区分完整验收图与局部图。
4. AMS/PMS/LocalServices 等代表性证据只适用于 Framework/AOSP 验收，不适用于任意局部源码。
5. 无 CodeQL DB 时，调用图和跨方法数据流会正确降级；局部源码功能必须保留这一边界。

## 3. Non-goals

v0.1 不实现：

- 通用的非 AOSP CodeQL database builder；
- 缺失仓库的依赖下载或自动 Repo sync；
- 根据名称猜测缺失类型、调用目标或 Binder 实现；
- C/C++、Rust、HIDL、Native Binder 或 Soong Build Graph；
- Web 服务、PostgreSQL 投影或自然语言问答；
- 将 partial 图合并成伪完整 AOSP 图；
- 改变现有 Git-managed fact corrections 语义。

## 4. Chosen Model

### 4.1 Explicit analysis scope

`source_roots.default.toml` 和本机保留的 `source_roots.local.toml` 支持：

```toml
[workspace]
analysis_scope = "aosp"
```

或：

```toml
[workspace]
analysis_scope = "partial"
```

允许值只有：

- `aosp`：保留现有 Framework/AOSP 发布行为和代表性门禁；
- `partial`：针对显式配置的局部仓库，以通用数据门禁替代 Framework 专用门禁。

缺省值为 `aosp`，保证旧配置升级后不静默放宽门禁。未知值、空值和大小写变体均作为配置错误拒绝。

`analysis_scope` 与 `strict`/`strict_capability` 是正交维度：

- scope 描述图谱声称覆盖的源码范围；
- strict 描述请求能力缺口是否阻止发布。

`partial` 不等于低质量，也不自动关闭 strict。

### 4.2 Workspace plan identity

`WorkspaceConfig` 和 `WorkspacePlan` 增加：

```text
analysis_scope: Literal["aosp", "partial"]
```

执行计划稳定序列化：

```json
{
  "analysis_scope": "partial",
  "full_aosp_coverage": false,
  "repositories": [],
  "inventories": [],
  "tasks": []
}
```

`full_aosp_coverage` 在 v0.1 中是面向消费者的保守声明：

- `partial` 永远为 `false`；
- `aosp` 也不因 profile 名称或配置仓库处理完成而自动变为 `true`；只有独立的完整 manifest 验收才能证明该值。

为避免第二事实源，执行逻辑只读取 `analysis_scope`；v0.1 不使用 `full_aosp_coverage` 控制执行，它仅阻止消费者把 partial 图误认为完整 AOSP。

## 5. Validation Architecture

新增独立模块：

```text
workspace/source_scope_validation.py
```

同一模块提供 preflight 和 post-import 两阶段验证，并原子写入：

```text
data/workspace/source-scope-validation.json
```

### 5.1 Preflight validation

在创建 staged SQLite schema、运行 Ctags 或启动 JADX 之前执行。

所有 full rebuild 必须满足：

1. 至少一个 repository 被显式启用；
2. 每个启用仓库的状态都是 `available`；
3. 每个启用仓库的 inventory file count 大于零；
4. 至少检测到一种受识别语言；
5. 至少存在一个 `scheduled` parser task；
6. repository path/name 没有重复身份；
7. inventory SHA-256 和 revision state 已写入 plan。

以下命令只生成诊断信息，不执行 publication preflight：

```text
--discover-only
--plan-only
```

因此缺失源码仍可通过 plan 报告被定位；真正 full rebuild 会尽早失败。

### 5.2 Partial post-import validation

`analysis_scope = "partial"` 必须满足：

1. live candidate DB 至少有一个非 `GRAPH_BUILD` source-backed node；
2. source-backed node 至少属于一个启用仓库；
3. capability report 非空；
4. 每个 scheduled capability 都产生 `supported` 或显式 `degraded` 状态，不能停留在 `scheduled`/`not_executed`；
5. foreign-key、symbol collision、permission、provenance 和 correction 门禁全部继续执行；
6. requested strict capability 必须是 `supported`；
7. 不要求 `EXPOSED_AS_LOCAL_SERVICE`、AMS、PMS 或 SystemUI 代表性证据。

未支持语言保持 `unsupported`，缺少 CodeQL DB 时：

```text
call_graph = degraded
interprocedural_dataflow = degraded
```

这些状态允许 non-strict partial 发布，但不得被查询层显示为 supported。

### 5.3 AOSP post-import validation

`analysis_scope = "aosp"` 保留现有行为：

- 继续要求 `EXPOSED_AS_LOCAL_SERVICE`；
- 继续运行 AMS/PMS 查询和现有 strong-evidence/strict capability 门禁；
- 不因加入 partial profile 而放宽任何既有验收。

本阶段只把现有 LocalServices shell 判断迁移到 profile-aware validator；不额外扩大 AOSP 验收范围。

### 5.4 Empty and stale source safety

以下情况必须在 publication 前失败并保持旧 live DB 不变：

- 没有启用仓库；
- 启用仓库缺失；
- 仓库为空或全部被 include/exclude 过滤；
- 没有 scheduled parser；
- parser 完成但没有产生 source-backed node；
- source inventory 在构建期间发生变化；
- strict capability 没有观察到约定证据；
- scope report 与 plan、provenance 或 build manifest 不一致。

## 6. Publication and Provenance

### 6.1 Scope validation report

报告采用稳定、可哈希的结构：

```json
{
  "schema_version": 1,
  "build_id": "20260824T000000Z-1234-abcd1234",
  "analysis_scope": "partial",
  "full_aosp_coverage": false,
  "status": "passed",
  "enabled_repositories": [
    {
      "name": "platform/frameworks/base",
      "path": "frameworks/base",
      "revision": "...",
      "inventory_sha256": "...",
      "file_count": 33273
    }
  ],
  "scheduled_task_count": 12,
  "source_backed_node_count": 574398,
  "capability_counts": {
    "degraded": 2,
    "supported": 9,
    "unsupported": 9
  },
  "validation_errors": []
}
```

所有数组和映射按稳定键排序；时间戳不参与语义内容哈希。

### 6.2 Build artifacts

以下位置必须保存 scope payload 或其内容哈希：

- `execution-plan.json`；
- `source-scope-validation.json`；
- `provenance.json`；
- `build-manifest.json`；
- `GRAPH_BUILD.properties_json`；
- retained history workspace；
- graph fingerprint 输入。

`build_publish prepare` 必须验证 scope report：

- build ID 与 staged batch 一致；
- repository set 与 plan/provenance 完全一致；
- report status 为 `passed`；
- scope report SHA-256 与 manifest/GRAPH_BUILD 一致。

### 6.3 Query visibility

新增：

```text
queries/source_scope_summary.sql
```

partial 图输出必须包含明显边界：

```text
analysis_scope     partial
full_aosp_coverage 0
warning            PARTIAL SOURCE GRAPH - NOT FULL AOSP
```

查询不应根据节点数量猜测 coverage。

## 7. Pipeline Flow

```text
load default + local source config
        |
        v
discover repositories and language inventory
        |
        v
write execution plan
        |
        +---- discover-only / plan-only: stop with diagnostics
        |
        v
source-scope preflight
        |
        v
begin staged build
        |
        v
Java/Kotlin/AIDL/Inheritance/Service/Permission/Vendor/CodeQL
        |
        v
runtime capability coverage
        |
        v
source-scope post-import validation
        |
        v
provenance + fingerprint + FK + corrections
        |
        v
scope-aware build manifest and GRAPH_BUILD
        |
        v
atomic publish / retain old live DB on any failure
```

Scope post-validation must run after runtime capability coverage but before build publication.

## 8. Partial CodeQL Behavior

v0.1 does not create a generic CodeQL database from an unbuildable partial checkout.

Rules:

- no `--codeql-db`: publish base partial graph with call/dataflow explicitly degraded;
- supplied verified CodeQL DB: repository identity set must exactly match enabled plan repositories;
- mismatched or modified DB: reject before materialization;
- `--strict-capability call_graph` or `interprocedural_dataflow`: missing CodeQL DB fails and preserves live DB;
- never use `build-mode=none` as a Kotlin workaround.

A generic buildable-module CodeQL preparation flow is a separate future milestone.

## 9. Configuration Examples

### 9.1 Partial repositories under AOSP root

```toml
[workspace]
aosp_root = "/home/ts/aosp"
analysis_scope = "partial"
auto_discover_manifest = true
auto_enable_discovered = false

[repositories."frameworks/base"]
enabled = true
include = ["core", "services", "packages", "data"]

[repositories."frameworks/libs/systemui"]
enabled = true
languages = ["java", "kotlin", "aidl", "xml"]

[repositories."frameworks/native"]
enabled = true
languages = ["c", "cpp", "aidl"]
```

Native languages will be reported unsupported until a corresponding parser is implemented.

### 9.2 Arbitrary local repository

```toml
[workspace]
aosp_root = "/home/ts/aosp"
analysis_scope = "partial"
auto_discover_manifest = false

[[extra_repositories]]
name = "vendor-system-service"
path = "/home/ts/work/vendor-system-service"
enabled = true
languages = ["java", "kotlin", "aidl", "xml"]
```

Absolute paths remain part of local configuration only and are never copied back into canonical Git source.

## 10. Compatibility and Upgrade

- Existing configs without `analysis_scope` behave as `aosp`.
- `source_roots.local.toml` remains machine-owned and preserved during `--upgrade`.
- Installer payload adds default profile support without overwriting local repository choices.
- Existing SQLite node/edge IDs do not change merely because scope metadata is added.
- Existing query files remain valid.
- A new schema migration is only required if scope metadata needs typed storage; v0.1 prefers `GRAPH_BUILD.properties_json` plus verified reports to avoid unnecessary base-table churn.
- Existing `--strict`, `--strict-capability`, `--retain-history`, vendor input and correction behavior remain unchanged.

## 11. Error Handling

Errors are classified and reported with stable reason codes:

```text
no_enabled_repository
missing_enabled_repository
empty_repository_inventory
no_recognized_language
no_scheduled_parser
no_source_backed_node
capability_not_executed
scope_repository_mismatch
scope_provenance_mismatch
strict_capability_gap
aosp_representative_evidence_missing
```

Human-readable messages include repository path and capability. Reports retain structured errors when `--keep-failed-db` is requested. No error handler may publish a partial staged DB.

## 12. Test and Acceptance Matrix

### 12.1 Unit tests

- default missing scope resolves to `aosp`;
- `partial` and `aosp` parse and round-trip through plan JSON;
- unknown scope is rejected;
- preflight rejects no repository, missing enabled repository, empty inventory and no scheduled task;
- post-import rejects empty graph and unexecuted capability status;
- scope report ordering and hash are deterministic;
- scope report repository identities exactly match plan/provenance.

### 12.2 Integration tests

1. A Java-only partial repository without LocalServices publishes successfully.
2. Two partial repositories preserve repository-scoped definitions and cross-repository inheritance.
3. Partial AIDL repository can publish Binder symbols without AMS/PMS evidence.
4. Missing explicitly enabled repository fails before Ctags and preserves live DB.
5. Empty partial repository fails and preserves live DB.
6. Forced importer/permission/scope failure preserves live DB and optionally retains staging.
7. Partial build without CodeQL publishes with call/dataflow degraded.
8. Partial strict call graph fails without CodeQL.
9. `aosp` profile retains LocalServices validation.
10. Upgrade preserves local partial profile and repository selection.

### 12.3 Publication evidence

Acceptance requires:

- root and canonical project suites green;
- shell syntax and Python compile checks green;
- foreign-key check green;
- two independent fixture builds produce identical semantic fingerprint and scope hash;
- query output displays partial warning;
- failed fixture build leaves the prior live DB byte-identical;
- WSL local-source smoke build, if actual partial sources are available, records the exact repository revisions and limitations.

The WSL smoke build is evidence for the available local repositories only; it is not full AOSP acceptance.

## 13. Completion Boundary

Partial Source Workspace Profile v0.1 is complete only when:

- a non-Framework partial fixture publishes without Framework-specific gates;
- empty/missing/misconfigured partial inputs cannot replace live data;
- scope identity is visible in every publication and retained-history evidence path;
- strict capability behavior remains enforceable;
- the legacy AOSP profile does not regress;
- installation, upgrade and documentation contracts pass;
- all changes are committed, reviewed and merged through Git.

It does not make the entire AndroidContextIntelligence platform complete and does not claim full AOSP coverage.
