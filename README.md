# Android Context Intelligence

## 当前版本状态

Multi-Repository Source Configuration v0.1 已在现有 WSL 工程完成核心验收：6 个工作区单元测试和 1 个双仓库集成测试通过，严格模式按预期拒绝缺少解析器的 Kotlin 能力，全量图谱重建完成，SQLite 外键检查以及 AMS、PMS Binder 链路验证通过。

该版本已经可以用于配置和扫描多个 Java/AIDL 仓库。Kotlin、C/C++、Rust、HIDL 等语言会被检测并写入能力报告；没有对应语义解析器时不会被误报为已覆盖。

在进入 Permission Enforcement Graph v0.1 前，建议先完成三项工程加固：原子数据库替换、干净安装只执行一次最终重建，以及重复 qualified name 的无损来源记录。

Android Context Intelligence 用确定性程序分析手段，把 AOSP 源码转换为可查询的 Android 系统上下文图谱。当前版本不依赖大模型生成事实，重点覆盖 Java 符号、AIDL/Binder、Java 继承关系、System Service 注册关系，以及多仓库发现和解析器能力报告。

项目的长期目标是为 Android-specific Context Graph、Context Expander、CTS/XTS 根因分析和 Agent Loop Engine 提供可信的事实层。

## 一、是否需要复制 `android-context-current`

对于一份干净的 AOSP 源码，**不需要复制**：

```text
D:\AndroidContextIntelligence\android-context-current
```

这个目录是之前 WSL 工程的代码快照，主要用于开发和验证后续安装脚本，不是安装输入。

干净安装只需要复制工作区根目录的以下 5 个脚本：

```text
setup_android_context_intelligence_v1.sh
install_java_inheritance_graph_v01.sh
install_system_service_registration_graph_v01.sh
install_multi_repository_source_configuration_v01.sh
setup_android_context_intelligence_complete_v01.sh
```

其中推荐直接运行最后一个总入口。它会依次调用前四个脚本，在 `/home/ts/android-context-intelligence` 创建完整工程。

`android-context-current` 仅在以下场景有用：

- 检查之前已生成工程的源码；
- 对比安装脚本生成结果；
- 在没有 WSL 工程时进行接口审查；
- 备份或迁移已有的手工修改。

如果没有需要保留的手工改动，它不需要复制到 WSL。

## 二、实现原理

整体数据流如下：

```text
AOSP / repo manifest
        │
        ├── 仓库发现与 TOML 配置
        ├── 语言文件清单
        └── 解析器能力矩阵
                    │
                    ▼
          Workspace Execution Plan
                    │
       ┌────────────┼─────────────┐
       ▼            ▼             ▼
 Universal Ctags   AIDL Parser   Java Source Rules
       │            │             │
 Java Symbol       Binder        Service Registration
 Inheritance       Interface      Constant/Instance Resolution
       └────────────┼─────────────┘
                    ▼
              SQLite Graph
                    │
                    ├── 节点与关系查询
                    ├── Context Expander
                    ├── PASS/FAIL Diff
                    └── Agent/Verifier Loop
```

### 1. 事实优先

基础图谱由源码分析工具和确定性规则生成，AI 不参与基础事实构建。

```text
事实采集 → 关系归一化 → SQLite 图谱 → 上层推理
```

这样可以避免 AI 先生成错误架构关系、再基于错误关系继续推理。

### 2. 稳定节点身份

节点使用类型和稳定标识构成 ID，例如：

```text
JAVA_CLASS:com.android.server.am.ActivityManagerService
AIDL_INTERFACE:android.app.IActivityManager
BINDER_SERVICE_NAME:activity
```

方法节点包含所属类与签名：

```text
JAVA_METHOD:com.android.server.am.ActivityManagerService#systemReady(...)
```

这使得图谱能够在重复构建时执行 upsert，并支持后续增量更新和版本差异比较。

### 3. 分层图谱

当前已实现以下图层。

#### Java Symbol Graph

从 Universal Ctags JSON 提取：

- Java package、class、interface、enum；
- method、field、enum constant；
- `DECLARED_IN`；
- `HAS_METHOD`；
- `HAS_MEMBER`。

导入器会读取 Java `package` 声明，生成完整限定名，而不是只使用简单类名。

#### AIDL/Binder Graph

解析 AIDL interface 和方法，生成：

- `AIDL_INTERFACE`；
- `AIDL_METHOD`；
- `AIDL_HAS_METHOD`；
- `IMPLEMENTS_BINDER`。

例如：

```text
ActivityManagerService
    IMPLEMENTS_BINDER
IActivityManager
```

#### Java Inheritance Graph

使用 Ctags `inherits` 字段和全局 Java 类型索引生成：

- `EXTENDS`；
- `IMPLEMENTS_JAVA_INTERFACE`。

支持通过 SQLite 递归查询间接 Binder 实现，例如：

```text
PackageManagerService.IPackageManagerImpl
    EXTENDS
IPackageManagerBase
    IMPLEMENTS_BINDER
IPackageManager
```

#### System Service Registration Graph

扫描：

```java
ServiceManager.addService(...)
publishBinderService(...)
LocalServices.addService(...)
```

生成：

- `SERVICE_REGISTRATION`；
- `BINDER_SERVICE_NAME`；
- `LOCAL_SERVICE_KEY`；
- `REGISTERS_BINDER_NAME`；
- `REGISTERS_LOCAL_KEY`；
- `REGISTERS_INSTANCE`；
- `REGISTERED_AS`；
- `EXPOSED_AS_LOCAL_SERVICE`。

解析器会尽可能解析：

- 字符串字面量；
- 同文件常量；
- `Context.ACTIVITY_SERVICE` 等跨类常量；
- `new FooService()`；
- 局部变量；
- 字段变量；
- 内部类实例；
- 简单工厂方法返回类型。

无法确定的事实仍保留在 JSON 报告中，并标记为 unresolved，不会凭名称强行猜测。

#### Multi-Repository Source Configuration

多仓库层负责：

- 读取 `.repo/manifest.xml`；
- 处理 manifest include；
- 合并 `config/source_roots.toml`；
- 探测仓库使用的语言；
- 根据“语言 × 图能力”匹配解析器；
- 生成统一 execution plan；
- 调度所有已支持的仓库解析任务。

当前检测的语言包括：

```text
Java、AIDL、Kotlin、C、C++、Rust、HIDL、Python、Blueprint、Make、Proto
```

当前正式语义解析能力主要覆盖 Java 和 AIDL。Kotlin、C/C++、Rust、HIDL 等语言会进入结构化能力报告，但不会被错误标记为“已完整解析”。

## 三、5 个脚本分别做什么

### `setup_android_context_intelligence_v1.sh`

创建基础工程并生成：

- Python 虚拟环境；
- SQLite Schema；
- GraphWriter；
- Java Symbol Graph；
- AIDL/Binder Graph；
- 单元测试和基础查询；
- 初始 `scripts/rebuild_all.sh`。

单独使用场景：只需要验证 Java Symbol 和直接 Binder 关系。

### `install_java_inheritance_graph_v01.sh`

在基础工程上增加：

- `EXTENDS`；
- `IMPLEMENTS_JAVA_INTERFACE`；
- PMS 间接 Binder 递归查询；
- 对统一重建入口的集成。

单独使用场景：已经安装基础工程，需要补充类继承关系。

### `install_system_service_registration_graph_v01.sh`

增加 Binder Service 和 LocalServices 注册图。

单独使用场景：需要查询服务名、注册实例、Binder 接口和 Local Service Key 之间的关系。

### `install_multi_repository_source_configuration_v01.sh`

把原来只处理 `frameworks/base` 的流程升级为配置驱动的多仓库流程，增加：

- repo manifest 发现；
- TOML 仓库配置；
- 语言探测；
- 解析器能力矩阵；
- strict 模式；
- 多仓库 Java/AIDL/Inheritance/Service 调度；
- 每仓库 Ctags 数据；
- duplicate qualified-name 报告；
- 仓库元数据写入节点；
- 双仓库集成测试。

单独使用场景：前面三个阶段已经安装，需要把更多 AOSP、Mainline 或 vendor 仓库纳入图谱。

### `setup_android_context_intelligence_complete_v01.sh`

总安装入口，按顺序调用前四个脚本。

推荐用于：

- 干净 AOSP；
- 新 WSL 环境；
- 需要从零重建工具工程；
- 验证所有已完成图层可以组合安装。

## 四、环境要求

默认环境：

```text
WSL2 Ubuntu 24.04
AOSP: /home/ts/aosp
工具工程: /home/ts/android-context-intelligence
```

脚本不包含 `apt install`。需要提前安装：

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  git universal-ctags sqlite3 ripgrep jq graphviz
```

检查：

```bash
python3 --version
ctags --version
sqlite3 --version
rg --version
```

`ctags` 必须是 Universal Ctags。

## 五、干净 AOSP 的完整安装

### 1. 将 5 个脚本复制到 WSL

Windows 文件位于：

```text
D:\AndroidContextIntelligence
```

WSL 中执行：

```bash
mkdir -p /home/ts/android-context-installers

cp /mnt/d/AndroidContextIntelligence/*.sh \
  /home/ts/android-context-installers/

chmod +x /home/ts/android-context-installers/*.sh
```

### 2. 使用默认路径安装

```bash
cd /home/ts/android-context-installers
./setup_android_context_intelligence_complete_v01.sh --fresh
```

`--fresh` 会备份已有项目目录，然后重新创建工具工程，不会直接删除旧工程。

### 3. 使用自定义路径安装

```bash
cd /home/ts/android-context-installers

AOSP_ROOT=/path/to/aosp \
PROJECT_ROOT=/path/to/android-context-intelligence \
./setup_android_context_intelligence_complete_v01.sh --fresh
```

### 4. 在已有工具工程上重装/更新

```bash
cd /home/ts/android-context-installers
./setup_android_context_intelligence_complete_v01.sh --rebuild
```

## 六、已有工程的多仓库升级

如果基础、继承和 Service 图已经安装，只需执行：

```bash
cp /mnt/d/AndroidContextIntelligence/install_multi_repository_source_configuration_v01.sh \
  /home/ts/

chmod +x /home/ts/install_multi_repository_source_configuration_v01.sh
/home/ts/install_multi_repository_source_configuration_v01.sh
```

该脚本会备份当前 `rebuild_all.sh` 和文档，并运行：

- 单元测试；
- 双仓库集成测试；
- 非严格能力报告测试；
- strict 失败测试；
- Bash/Python 语法检查；
- 当前 AOSP 的完整图谱重建；
- SQLite 外键检查；
- AMS/PMS 查询验收。

## 七、日常使用命令

安装全部能力后，日常操作只需要进入工具工程：

```bash
cd /home/ts/android-context-intelligence
```

### 完整重建

源码更新、切换分支或修改仓库配置后执行：

```bash
./scripts/rebuild_all.sh
```

执行顺序：

```text
生成 Workspace Plan
→ 重置 SQLite DB
→ Java Symbol
→ AIDL/Binder
→ Java Inheritance
→ Service Registration
→ 仓库元数据标注
→ 外键检查
→ AMS/PMS 查询
```

### 只发现仓库和语言

```bash
./scripts/rebuild_all.sh --discover-only
```

不会删除或修改现有数据库。

### 只生成执行计划

```bash
./scripts/rebuild_all.sh --plan-only
```

不会删除或修改现有数据库。

### 严格模式

```bash
./scripts/rebuild_all.sh --strict
```

只要启用仓库中存在没有解析器覆盖的语言/能力，就会返回非零退出码。适合 CI 覆盖检查。

### 针对某项能力严格检查

```bash
./scripts/rebuild_all.sh \
  --strict-capability permission_enforcement
```

只把指定能力缺口作为失败条件。

### 指定其他仓库配置

```bash
./scripts/rebuild_all.sh \
  --source-config config/source_roots.toml
```

## 八、增加其他仓库

编辑：

```text
/home/ts/android-context-intelligence/config/source_roots.toml
```

例如启用 Permission Mainline 模块：

```toml
[repositories."packages/modules/Permission"]
enabled = true
languages = ["java", "kotlin", "aidl"]
```

限制扫描目录：

```toml
[repositories."vendor/example"]
enabled = true
name = "vendor-example"
include = ["framework", "service", "interfaces"]
exclude = ["tests", "prebuilt", "generated"]
languages = ["java", "kotlin", "aidl", "cpp"]
```

添加不在 repo manifest 中的目录：

```toml
[[extra_repositories]]
name = "local-extension"
path = "/home/ts/local/android-extension"
enabled = true
languages = ["java", "aidl"]
```

默认情况下，manifest 中发现的仓库不会全部自动启用：

```toml
auto_enable_discovered = false
```

这是为了避免第一次执行就扫描数百个 AOSP 仓库。需要分析的仓库应在 TOML 中显式启用。

## 九、查询图谱

数据库位置：

```text
/home/ts/android-context-intelligence/data/android_context.db
```

### 节点统计

```bash
sqlite3 -header -column data/android_context.db \
  < queries/summary.sql
```

### AMS Binder 关系

```bash
sqlite3 -header -column data/android_context.db \
  < queries/ams_binder.sql
```

### AMS Service 完整链

```bash
sqlite3 -header -column data/android_context.db \
  < queries/ams_service_chain.sql
```

### PMS 继承与 Binder 链

```bash
sqlite3 -header -column data/android_context.db \
  < queries/pms_service_chain.sql
```

### LocalServices 汇总

```bash
sqlite3 -header -column data/android_context.db \
  < queries/local_services_summary.sql
```

### 仓库覆盖统计

```bash
sqlite3 -header -column data/android_context.db \
  < queries/workspace_coverage_summary.sql
```

### 外键完整性

```bash
sqlite3 data/android_context.db \
  'PRAGMA foreign_key_check;'
```

无输出表示外键完整。

## 十、主要输出产物

### Workspace 层

```text
data/workspace/repositories.json
data/workspace/language-inventory.json
data/workspace/capability-report.json
data/workspace/execution-plan.json
```

### 原始解析报告

```text
data/raw/ctags/<repository>.jsonl
data/raw/ctags/duplicate-qualified-names.json
data/raw/aidl/aidl-binder-report.json
data/raw/inheritance/<repository>.json
data/raw/service/service-registration-report.json
```

### 图数据库

```text
data/android_context.db
```

## 十一、适用场景

### Android Framework 服务理解

查询一个 System Service 的：

```text
实现类 → 继承关系 → Binder AIDL → Service 名称 → 源文件
```

### CTS/XTS 根因分析基础

根据测试、异常 API 或 Service 名称路由到相关类、AIDL、权限和配置。Permission Enforcement Graph 完成后，可继续扩展权限检查路径。

### Framework 修改影响分析

确认某个类属于哪些服务关系，以及修改可能影响哪些 Binder 接口和注册入口。

### PASS/FAIL 系统差异分析

结合未来 Runtime Snapshot 和 Build Graph，对比两套系统的静态结构与运行状态。

### Android System Agent

为 Context Expander、Reasoner、Executor 和 Verifier 提供确定性上下文，避免让 Agent 直接在整个 AOSP 中盲目搜索。

### Vendor/Mainline 扩展分析

在 TOML 中启用 `packages/modules/*`、`vendor/*` 或本地扩展仓库，建立跨仓库 Java/AIDL/继承/服务关系。

## 十二、当前限制

### 1. 语言解析能力

当前正式图谱解析器主要支持：

```text
Java
AIDL
```

以下语言可以探测和报告，但尚未提供完整语义解析器：

```text
Kotlin
C/C++
Rust
HIDL
```

因此发现这些语言不等于已经建立对应的 Symbol、Inheritance、Native Binder 或 Permission 图。

### 2. 不是完整 Java 编译器

Java Symbol 和继承关系主要来自 Ctags 与源码规则，对以下结构可能无法完全解析：

- 匿名类；
- 局部类；
- 复杂泛型；
- 生成源码；
- 编译期条件分支；
- 产品特定 Soong variant。

无法解析的关系会进入报告，不应被视为不存在。

### 3. 尚未包含 Build Graph

当前不能完整回答：

```text
Source → Soong Module → Variant → Ninja Action → Artifact → Partition → Image
```

这需要后续接入 Soong module graph、module actions、`module-info.json` 和 Ninja 查询结果。

### 4. 尚未包含 Runtime Graph

当前主要是静态源码图，不代表设备实时状态。以下信息需要后续通过 ADB/Perfetto 采集：

- 实际运行 Service；
- UID 与权限授予状态；
- Active APEX；
- VINTF；
- SELinux denial；
- Binder/Perfetto trace。

### 5. 数据库重建方式

当前 canonical rebuild 会重新创建 SQLite 数据库，再依次生成各图层。多仓库规模增大后，应增加文件 Hash、Graph Patch 和增量更新机制。

## 十三、故障排查

### 找不到 AOSP

```text
Missing AOSP root
```

确认：

```bash
ls /home/ts/aosp
ls /home/ts/aosp/frameworks/base
```

或显式设置：

```bash
AOSP_ROOT=/actual/aosp/path ./setup_android_context_intelligence_complete_v01.sh --fresh
```

### Ctags 不正确

```bash
ctags --version
```

输出必须包含：

```text
Universal Ctags
```

### 查看测试

```bash
cd /home/ts/android-context-intelligence
source .venv/bin/activate
python -m pytest -q
```

### 查看能力缺口

```bash
jq '.[] | select(.status != "scheduled")' \
  data/workspace/capability-report.json
```

### 查看重复限定名

```bash
jq '.' data/raw/ctags/duplicate-qualified-names.json
```

### 检查 canonical rebuild 语法

```bash
bash -n scripts/rebuild_all.sh
```

## 十四、推荐工作流

首次安装：

```bash
./setup_android_context_intelligence_complete_v01.sh --fresh
```

增加仓库：

```bash
vim /home/ts/android-context-intelligence/config/source_roots.toml
```

先查看计划：

```bash
./scripts/rebuild_all.sh --plan-only
```

检查能力报告：

```bash
jq '.' data/workspace/capability-report.json
```

执行完整重建：

```bash
./scripts/rebuild_all.sh
```

最后检查：

```bash
sqlite3 data/android_context.db 'PRAGMA foreign_key_check;'
```

日常不需要再次运行各个 `install_*.sh`。安装脚本只负责向干净工具工程安装能力；源码同步、分支切换和仓库配置变化后，统一运行 `scripts/rebuild_all.sh`。
