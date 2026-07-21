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
AOSP repo manifest + source_roots.toml
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
- upgrade 保留 `.venv/`、`data/`、`config/source_roots.toml` 和 `configs/local.yaml`，同时保留旧源码 rollback。
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
| `installers/install_*_v01.sh` | 兼容旧命令，默认转发 `--upgrade` | 仅用于迁移旧自动化，后续删除 |
| `project/scripts/import_vendor.sh` | 当前实验性 Vendor 反编译/导入入口 | 仅在数据库副本上验证，见风险说明 |

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
bash scripts/rebuild_all.sh --strict-capability permission_enforcement
```

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

编辑部署目录中的 `config/source_roots.toml`；该文件在 upgrade 时保留。

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

## 8. Vendor/JADX 当前边界

WSL 中已有 JADX 1.5.6 和 A17 jar/apk 输入并不等于 Vendor 图已经可靠发布。当前 `import_vendor.sh` 仍有以下未解决风险：

- 直接修改 live SQLite，而不是进入 rebuild staging/lock/atomic publish；
- 输入、反编译缓存与生成报告的生命周期未完全分离；
- 尚未用 artifact SHA-256 + JADX 版本/状态决定缓存复用；
- 现有 live 数据库没有证据支持历史 README 的大规模 Vendor 完成声明。

因此在 Vendor 原子化里程碑完成前，只在数据库副本和单独输出目录中实验，不要对唯一生产库直接执行。参考 [仓库架构审查](doc/reviews/2026-07-21-repository-architecture-review.md)。

## 9. 当前已验证状态和限制

本分支的发布基线验证：

```text
根安装/发布契约：44 passed
规范项目测试：54 passed
```

2026-07-21 审查时的 live 数据库仅显示 27 条 `REQUIRES_PERMISSION`、8 条 `ENFORCES_PERMISSION`，未建立 XML permission declaration 边；全部 574,399 个节点的 `source_revision` 仍为 `unknown`。因此 Permission、Vendor 和 provenance 均是后续工作，不是已完成能力。

其他限制：

- C/C++、Rust、HIDL 目前只有语言探测，没有对应语义解析器。
- Kotlin 使用 Ctags/启发式解析，继承和复杂语法覆盖有限。
- 尚无精确方法调用图、Soong Build Graph、Runtime/Test Graph。
- Permission 扫描仍缺常量表达式、多行语法和可靠方法范围。

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

1. 完成临时 WSL fresh/upgrade/verify 的非破坏性验收。
2. 修正 Permission XML 调度、常量/多行语义和方法范围。
3. 将 Vendor 输入移出发布输出并接入 staging、锁、验证和原子发布。
4. 记录每个 repo revision/dirty state、JADX 版本和 artifact SHA-256。
5. 再建设 Build Graph、增量更新、Runtime/Test Graph 和 Agent 上下文接口。

## 12. 文档

- [文档索引](doc/README.md)
- [仓库架构审查](doc/reviews/2026-07-21-repository-architecture-review.md)
- [可信源码与安装设计](doc/designs/2026-07-21-trustworthy-source-and-installation-baseline-design.md)
- [可信源码与安装实施计划](doc/plans/2026-07-21-trustworthy-source-and-installation-baseline-plan.md)
- [总体架构](doc/architecture/android-specific-context-graph.md)
- [最终技术方案](doc/architecture/Android_Context_Graph_Final_Technical_Plan.md)
