# Android Context Intelligence

Android Context Intelligence 使用确定性程序分析，把 AOSP 源码转换为可查询的 SQLite 上下文图谱。当前重点是 Java/Kotlin 符号、AIDL/Binder、继承、系统服务注册、多仓库计划和原子数据库发布；AI 不参与基础事实生成。

本仓库已经改为“可读规范源码 + 薄安装器”结构。`project/` 是唯一实现源码，`~/android-context-intelligence` 只是可丢弃的 WSL 部署目录。

## 1. 仓库结构与实现原理

```text
AndroidContextIntelligence/
├── project/                    # 唯一规范项目源码
│   ├── collectors/             # Java/AIDL/Service/Permission/Vendor 采集器
│   ├── workspace/              # 仓库发现、能力计划、流水线与原子发布
│   ├── graph/                  # 图节点、边与 SQLite writer
│   ├── storage/                # SQLite schema
│   ├── queries/                # 预置查询
│   ├── scripts/                # 重建与实验性 Vendor 入口
│   └── tests/                  # 规范项目单元/集成测试
├── scripts/
│   ├── project_payload.py      # managed payload 选择、哈希与比较
│   ├── install_project.py      # fresh/upgrade/verify 的事务式安装逻辑
│   └── verify_project_install.py
├── installers/
│   ├── install_project.sh      # 唯一薄安装适配器
│   └── install_*_v01.sh        # 一期兼容包装器，只转发到 setup.sh
├── setup.sh                    # 用户唯一入口
├── tests/                      # 仓库、发布和安装契约测试
└── doc/                        # 架构、设计、计划、审查与验收证据
```

主数据流：

```text
AOSP repo manifest + source_roots.default.toml + source_roots.local.toml
                │
                ▼
仓库发现 → 语言清单 → 解析器能力矩阵 → execution plan
                │
       ┌────────┼─────────┐
       ▼        ▼         ▼
  Universal   AIDL     Java/Kotlin
    Ctags     parser     source rules
       └────────┼─────────┘
                ▼
       staging build batch
                │ validation gates
                ▼
       atomic SQLite publication
```

关键边界：

- `project/` 是唯一规范源码；安装器不再保存 heredoc/base64 的第二份源码。
- payload 使用稳定相对路径和 SHA-256 清单管理。
- fresh 在目标同父目录 staging 并验证后 rename；已有目标不会被覆盖。
- upgrade 保留 `.venv/`、`data/`、`config/source_roots.local.toml` 和 `configs/local.yaml`，同时保留旧源码 rollback。
- WSL 部署目录不得反向同步为源码，也不是测试输入。
- 图层“代码已存在”不表示语义覆盖已经达标；以构建报告和数据库查询为准。

## 2. 干净 AOSP 环境需要复制什么

不能只复制 5 个或 6 个 `*.sh`。这些脚本已经是薄包装器，不包含 Python、SQL、TOML 和测试源码。

推荐复制完整 Git checkout 或正式发布包。若只做最小安装，至少必须一起保留：

```text
setup.sh
installers/
scripts/
project/
```

开发、测试和审计还应保留：

```text
tests/
doc/
pyproject.toml
```

以下内容不需要、也不应复制：

```text
android-context-current/              # 历史快照，不是源码
~/android-context-intelligence/data/  # WSL 运行输出
~/android-context-intelligence/.venv/ # WSL 运行环境
反编译缓存、pytest 缓存、安装 rollback
```

Windows 仓库可直接从 WSL 的 `/mnt/d/AndroidContextIntelligence` 调用；如需更稳定的 Linux I/O，可把完整 checkout（排除 `.git/.worktrees`）复制到 `/home/ts/android-context-installers`，但仍要保留上述目录结构。

## 3. 环境要求

基础安装/升级/校验只依赖 Bash 和 Python 3.11+ 标准库。显式执行全量 AOSP 重建还需要：

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  git universal-ctags sqlite3 ripgrep util-linux
```

检查：

```bash
python3 --version
ctags --version        # 必须是 Universal Ctags
sqlite3 --version
rg --version
flock --version
```

默认路径：

```text
AOSP_ROOT=$HOME/aosp
PROJECT_ROOT=$HOME/android-context-intelligence
```

## 4. 安装与升级命令

在仓库根目录执行。每次必须且只能选择一种模式。

### 新安装（不扫描 AOSP）

```bash
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --fresh
```

`--fresh` 要求目标不存在。若已有工程，请使用 `--upgrade`，或先自行重命名旧目录。

### 升级规范源码

```bash
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --upgrade
```

升级成功后，旧的受管源码保留在目标同级的隐藏目录：

```text
.install-rollback-android-context-intelligence-<id>/
```

### 校验已安装源码漂移

```bash
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --verify-only
```

或直接运行：

```bash
python3 scripts/verify_project_install.py \
  --target /home/ts/android-context-intelligence
```

退出码：`0` 表示一致，`1` 表示 added/removed/modified 漂移，`2` 表示输入或清单错误。

### 安装并显式全量重建 AOSP 图谱

```bash
AOSP_ROOT=/home/ts/aosp \
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --fresh --rebuild
```

已有部署升级并重建：

```bash
AOSP_ROOT=/home/ts/aosp \
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --upgrade --rebuild
```

只有 `--rebuild` 会校验 AOSP/ctags/sqlite/rg/flock、创建 `.venv`、安装 `requirements-lock.txt` 并运行全量扫描。该操作可能耗时数分钟到数小时。

从不含 `.git` 的发布包安装时，可显式记录来源版本：

```bash
ANDROID_CONTEXT_SOURCE_COMMIT=<release-commit> \
PROJECT_ROOT=/home/ts/android-context-intelligence \
bash ./setup.sh --fresh
```

## 5. 脚本职责和使用场景

| 脚本 | 作用 | 使用场景 |
|---|---|---|
| `setup.sh` | 解析模式、路径和显式 rebuild；调用唯一安装器 | 日常安装、升级、校验 |
| `installers/install_project.sh` | 定位仓库后 `exec` Python 安装器 | 自动化系统或调试安装层 |
| `scripts/install_project.py` | staging、hash manifest、fresh/upgrade、rollback | 安装实现和测试，不手工修改部署 |
| `scripts/verify_project_install.py` | 校验已安装 managed payload | 检查 WSL 是否被直接修改 |
| `project/scripts/rebuild_all.sh` | 多仓库计划、各解析器、验证和原子发布 | 安装后日常重建 |
| `project/scripts/profile_service_registration.py` | 在隔离数据库副本上执行 Service 冷/热 profile，比较语义指纹与验收链 | 调整 Service 扫描、解析或缓存前后 |
| `project/scripts/graph_fingerprint.py` | 对稳定节点与边做确定性 SHA-256，排除 build identity 和更新时间 | 比较两次独立全量构建是否产生相同图语义 |
| `installers/install_*_v01.sh` | 兼容旧命令，默认转发 `--upgrade` | 仅用于迁移旧自动化，后续删除 |
| `project/scripts/import_vendor.sh` | Vendor 兼容入口，转发到 canonical staged rebuild | 旧自动化迁移；不直接写 live DB |

## 6. 安装后的常用命令

```bash
cd /home/ts/android-context-intelligence

# 只刷新仓库/语言发现
bash scripts/rebuild_all.sh --discover-only

# 只生成执行计划
bash scripts/rebuild_all.sh --plan-only

# 原子全量重建
bash scripts/rebuild_all.sh

# 保留失败 staging 便于诊断
bash scripts/rebuild_all.sh --keep-failed-db

# 严格覆盖检查
bash scripts/rebuild_all.sh --strict
bash scripts/rebuild_all.sh --strict-capability permission_semantics

# 将 data/ 外部的 APK/JAR 纳入同一次原子重建
bash scripts/rebuild_all.sh \
  --vendor-input /home/ts/vendor-input \
  --jadx-bin /home/ts/jadx-1.5.6/bin/jadx

# 在隔离副本上验证 Service 缓存收益；不会修改 live DB
python scripts/profile_service_registration.py \
  --plan data/workspace/execution-plan.json \
  --db data/android_context.db \
  --cache-dir .cache/service-registration-profile \
  --output data/workspace/service-registration-profile.json

# 比较两次独立构建时，对各自发布的 live DB 计算完整语义指纹
python scripts/graph_fingerprint.py --db data/android_context.db
```

Service profiler 会先从数据库副本移除旧 Service 图，再分别执行冷缓存和热缓存
导入。只有两次图指纹一致，结果才有效；报告同时保留 AMS、PMS、LocalServices
计数、解析状态汇总、候选/排除文件数以及各阶段耗时。

全图指纹覆盖除 `GRAPH_BUILD` 外的稳定节点、边及其 provenance 字段，并忽略
`updated_at`。它用于比较两个独立构建结果，不能替代外键、能力覆盖和代表性 AOSP
证据门禁。

数据库与报告：

```text
data/android_context.db
data/workspace/repositories.json
data/workspace/language-inventory.json
data/workspace/capability-report.json
data/workspace/execution-plan.json
data/workspace/build-manifest.json
```

查询示例：

```bash
sqlite3 -header -column data/android_context.db < queries/ams_service_chain.sql
sqlite3 -header -column data/android_context.db < queries/pms_service_chain.sql
sqlite3 -header -column data/android_context.db < queries/local_services_summary.sql
sqlite3 data/android_context.db 'PRAGMA foreign_key_check;'
```

## 7. 添加其他 AOSP 仓库

复制 `config/source_roots.local.toml.example` 为
`config/source_roots.local.toml` 后编辑；该文件在 upgrade 时保留。

```toml
[repositories."packages/modules/Permission"]
enabled = true
languages = ["java", "kotlin", "aidl"]

[repositories."vendor/example"]
enabled = true
include = ["framework", "service", "interfaces"]
exclude = ["tests", "prebuilt", "generated"]
```

未支持语言不会静默当作成功：默认写入能力报告并继续，`--strict` 或 `--strict-capability` 可使覆盖缺口失败。

## 8. Vendor/JADX staged 导入

Vendor 输入目录必须位于 `data/` 外。默认目录为 `vendor-input/`，默认内容寻址
缓存为 `.cache/vendor-artifacts/`；两者都不是受管 payload，也不会作为 live
报告发布。缓存键由 artifact SHA-256、JADX 路径/版本和反编译选项共同决定。

每个构建在 `data/staging/<build-id>` 内完成反编译清单、Ctags、继承解析和
artifact 追踪。只有全部发布门禁通过后才替换 live DB。JADX 非零退出但仍产生
源码时记录为 `degraded`；没有可用源码时构建失败，live DB 保持不变。

主要证据：

```text
data/workspace/vendor-artifacts.json
data/raw/vendor/vendor-import-report.json
data/workspace/provenance.json
data/workspace/build-manifest.json
```

`VENDOR_ARTIFACT`、`DERIVED_FROM_ARTIFACT`、Vendor definition 的
`source_revision` 和继承边中的 `artifact_sha256` 可回溯每条 Vendor 定义。
`scripts/import_vendor.sh [INPUT_DIR]` 仅为兼容包装器，会调用
`scripts/rebuild_all.sh --vendor-input ...`，不能指定或修改 live DB。

## 9. 当前已验证状态和限制

本分支的发布基线验证：

```text
根安装/发布契约：54 passed
规范项目测试：145 passed
```

2026-07-21 审查时的 live 数据库仅显示 27 条 `REQUIRES_PERMISSION`、8 条
`ENFORCES_PERMISSION`，未建立 XML permission declaration 边；全部 574,399
个节点的 `source_revision` 仍为 `unknown`。这是 Permission Semantics Graph
实施前的历史快照，不代表当前分支；当前 Permission 能力和验收见第 13 节。
Vendor 原子导入和可复现 source/tool provenance 已接入 staged publication；
最终双构建指纹验收已通过，见
[可信多源导入验收](doc/reviews/2026-08-04-trustworthy-multi-source-ingestion-v01-acceptance.md)。

其他限制：

- C/C++、Rust、HIDL 目前只有语言探测，没有对应语义解析器。
- Kotlin 使用 Ctags/启发式解析，继承和复杂语法覆盖有限。
- 尚无精确方法调用图、Soong Build Graph、Runtime/Test Graph。
- Permission v0.1 仍会把无法解析的表达式、声明冲突和不支持构造显式写入报告。

## 10. 测试与开发

```bash
cd /mnt/d/AndroidContextIntelligence
source /home/ts/android-context-intelligence/.venv/bin/activate

python -m pytest -q
python -m pytest -q project/tests
python -m compileall -q project scripts
find . -name '*.sh' -type f -print0 | xargs -0 -n1 bash -n
git diff --check
```

所有修改通过 Git 功能分支管理。不要直接编辑 WSL 部署目录；应修改仓库的 `project/`，提交后执行 `--upgrade`。

## 11. 下一步顺序

可信多源导入 v0.1 已完成。后续新增 Build Graph、增量更新、Runtime/Test
Graph 或 Agent 上下文接口前，应先为目标域确认设计和验收门禁，并复用本阶段的
能力质量、repository-scoped identity、provenance、staging 和双构建确定性契约。

## 12. 文档

- [文档索引](doc/README.md)
- [仓库架构审查](doc/reviews/2026-07-21-repository-architecture-review.md)
- [Permission 后仓库架构复核](doc/reviews/2026-07-28-post-permission-repository-architecture-review.md)
- [可信多源导入实施计划](doc/plans/2026-07-28-trustworthy-multi-source-ingestion-v01-plan.md)
- [可信多源导入验收](doc/reviews/2026-08-04-trustworthy-multi-source-ingestion-v01-acceptance.md)
- [可信源码与安装设计](doc/designs/2026-07-21-trustworthy-source-and-installation-baseline-design.md)
- [可信源码与安装实施计划](doc/plans/2026-07-21-trustworthy-source-and-installation-baseline-plan.md)
- [总体架构](doc/architecture/android-specific-context-graph.md)
- [最终技术方案](doc/architecture/Android_Context_Graph_Final_Technical_Plan.md)

## 13. Permission Semantics Graph v0.1

`permission_semantics` 从启用仓库的 XML、Java 和 Kotlin 源码建立权限事实，覆盖：

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

因此，`ALLOWLISTS_PRIVILEGED_PERMISSION` 不能解释成应用已经获得权限；
`CHECKS_PERMISSION` 也不能与会拒绝调用的 `ENFORCES_PERMISSION` 混为一谈。

严格构建、摘要查询和确定性指纹：

```bash
bash scripts/rebuild_all.sh --strict-capability permission_semantics
sqlite3 -header -column data/android_context.db \
  < queries/permission_semantics_summary.sql
python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --fingerprint
```

原子重建先在 `data/staging/<build-id>/raw/permission/` 生成
`permission-semantics-report.json`，验证通过后才发布 live 数据库。

现有部署执行 `--upgrade` 时会把旧 `config/source_roots.toml` 校验并迁移为
`config/source_roots.local.toml`。canonical 默认值位于
`config/source_roots.default.toml`，并始终包含 `frameworks/base/data`；本地
include/exclude/languages 选择在升级后继续保留。

## 14. Java/Kotlin 精确调用图与跨方法数据流

该能力以现有 Ctags/AIDL/Service/Permission 图为稳定底座，用 CodeQL `java-kotlin`
数据库补充精确调用点、候选目标、有界跨方法数据流、安全 guard 与 Binder identity 证据。
CodeQL 是带 provenance 的精度适配层，不替代基础图，也不使用会排除 Kotlin 的
`build-mode=none`。

首次使用，或 AOSP revision、产品、variant、目标发生变化时，执行重型数据库准备：

```bash
cd /home/ts/android-context-intelligence
bash scripts/prepare_codeql.sh \
  --aosp-root /home/ts/aosp \
  --codeql-bin /home/ts/.local/share/codeql/v2.26.3/codeql/codeql \
  --product aosp_cf_x86_64_phone \
  --variant userdebug \
  --build-target services \
  --build-target SystemUI \
  --cache-root /home/ts/.cache/android-context-codeql
```

准备脚本会打印 verified CodeQL database 路径。随后执行原子重建：

```bash
bash scripts/rebuild_all.sh \
  --codeql-db /path/to/verified/database \
  --codeql-bin /home/ts/.local/share/codeql/v2.26.3/codeql/codeql \
  --strict-capability call_graph \
  --retain-history
```

不传 `--codeql-db` 时仍发布基础图，但 `call_graph` 和
`interprocedural_dataflow` 明确标记为 `degraded`；请求这两个 strict capability
时，缺少或不匹配的 CodeQL DB 会在 publication 前失败。调用关系区分 `MUST_CALL`、
`MAY_CALL` 与未解析调用；`OBSERVED_CALL` 保留给未来运行时证据。

跨方法数据流来自 CodeQL PathGraph 的 SARIF `codeFlows/threadFlowLocations`，每个
`dataflow_step` 都对应真实路径节点和源码位置，不以两个端点冒充完整路径。查询、
本地 QLL 依赖和 Python 解码器哈希共同参与缓存身份。Binder identity 只有在敏感
sink 被证明位于 clear/restore 区域内时才附着到安全轨迹。严格验收还会检查
`config/codeql.toml` 中 AMS、PMS、Kotlin SystemUI 的精确调用、安全路径和已提交负例。

CodeQL 数据库清单绑定精确仓库集合、构建参数、CLI/extractor 身份，以及排除运行时
cache/log 后的数据库实际文件清单、字节数和内容 SHA-256；数据库内容被修改后不会
作为 verified cache 复用。

纠错文件存放在 `config/corrections/*.toml`，支持 `suppress`、`replace`、
`annotate`、`add`。原始事实不删除；有效视图叠加 `FACT_CORRECTION`，hash/revision
不匹配会标记 stale。比较两次图谱：

```bash
python scripts/graph_diff.py \
  --before data/history/<old>/android_context.db \
  --after data/android_context.db \
  --format json
```

当前范围不包含 C/C++/Rust、Native Binder、任意全程序污点传播或运行时调用观测。
历史目录默认只保留报告；只有显式传入 `--retain-history-database` 才复制 SQLite。
