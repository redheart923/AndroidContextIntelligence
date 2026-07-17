# Android Context Intelligence

用确定性程序分析手段，把 AOSP 源码和厂商闭源包转换为可查询的 Android 系统上下文图谱（SQLite）。图谱由源码分析工具和确定性规则生成，AI 不参与基础事实构建，避免幻觉污染。

项目的长期目标是为 Android-specific Context Graph、Context Expander、CTS/XTS 根因分析和 Agent Loop Engine 提供可信的事实层。

## 一、项目结构

```text
AndroidContextIntelligence/
├── setup.sh                          # 唯一总入口，一键安装全部能力
├── installers/                       # 各图层的安装脚本（由 setup.sh 调用）
│   ├── setup_android_context_intelligence_v1.sh
│   ├── install_java_inheritance_graph_v01.sh
│   ├── install_system_service_registration_graph_v01.sh
│   ├── install_multi_repository_source_configuration_v01.sh
│   ├── install_vendor_customization_graph_v01.sh
│   └── install_permission_enforcement_graph_v01.sh
├── android-context-current/          # 开发快照（不需要复制到 WSL）
├── doc/                              # 架构设计、可行性分析、实施计划
│   ├── architecture/
│   ├── designs/
│   ├── feasibility/
│   └── plans/
├── scripts/                          # 工具脚本
└── tests/                            # 验证测试
```

## 二、实现原理

### 数据流总览

```text
AOSP 源码 + 厂商闭源包 (.jar/.apk)
              │
    ┌─────────┼──────────────┐
    ▼         ▼              ▼
 仓库发现    语言探测      Jadx 反编译
 TOML 配置   能力矩阵      Vendor 源码
              │
              ▼
    Workspace Execution Plan
              │
    ┌─────────┼──────────┐
    ▼         ▼          ▼
 Ctags     AIDL 解析   Java 源码规则
    │         │          │
 符号/继承  Binder 接口  Service 注册
    └─────────┼──────────┘
              ▼
        SQLite Graph (android_context.db)
              │
              ├── SQL 直接查询
              ├── AI/MCP 上下文投喂
              └── 跨域分析（AOSP ↔ Vendor）
```

### 核心设计原则

1. **事实优先**：基础图谱由源码分析工具和确定性规则生成，AI 不参与事实构建。
2. **稳定节点身份**：节点使用 `类型:限定名` 构成 ID（如 `JAVA_CLASS:com.android.server.am.ActivityManagerService`），支持 upsert、增量更新和版本差异比较。
3. **分层图谱**：各图层独立构建，可组合叠加。

### 已实现的图层

| 图层 | 节点类型 | 边类型 | 数据来源 |
|------|---------|--------|---------|
| Java/Kotlin Symbol | CLASS, INTERFACE, ENUM, METHOD, FIELD, OBJECT, TYPEALIAS | DECLARED_IN, HAS_METHOD, HAS_MEMBER | Universal Ctags |
| AIDL/Binder | AIDL_INTERFACE, AIDL_METHOD | AIDL_HAS_METHOD, IMPLEMENTS_BINDER | AIDL 源码解析 |
| Java Inheritance | 复用 Symbol 节点 | EXTENDS, IMPLEMENTS_JAVA_INTERFACE | Ctags inherits 字段 |
| System Service Registration | SERVICE_REGISTRATION, BINDER_SERVICE_NAME, LOCAL_SERVICE_KEY | REGISTERS_BINDER_NAME, REGISTERS_INSTANCE, REGISTERED_AS, EXPOSED_AS_LOCAL_SERVICE | Java 源码规则 |
| Multi-Repository | REPOSITORY 元数据 | — | repo manifest + TOML 配置 |
| Vendor Customization | 复用 Symbol/Inheritance 节点 | 自动桥接 EXTENDS/IMPLEMENTS 到 AOSP 基线 | Jadx 反编译 + Ctags |

### 语言支持状态

| 语言 | 符号提取 | 继承关系 | 说明 |
|------|---------|---------|------|
| Java | ✅ 完整 | ✅ 完整 | 主力解析 |
| Kotlin | ✅ 完整 | ⚠️ 部分 | 符号完整，Ctags 尚不支持 inherits 字段 |
| AIDL | ✅ 完整 | — | 接口和方法完整提取 |
| C/C++, Rust, HIDL | 🔍 探测 | — | 可探测并报告，尚无语义解析器 |

## 三、安装脚本说明

根目录只保留一个总入口 `setup.sh`，其余脚本位于 `installers/` 目录。

### `setup.sh`（总入口）

按顺序调用 `installers/` 目录下的 6 个脚本，在目标路径创建完整工程。

### `installers/setup_android_context_intelligence_v1.sh`

创建基础工程：Python 虚拟环境、SQLite Schema、GraphWriter、Java Symbol Graph、AIDL/Binder Graph、单元测试。

### `installers/install_java_inheritance_graph_v01.sh`

增加 `EXTENDS` 和 `IMPLEMENTS_JAVA_INTERFACE` 边、PMS 间接 Binder 递归查询。

### `installers/install_system_service_registration_graph_v01.sh`

增加 Binder Service 和 LocalServices 注册图。

### `installers/install_multi_repository_source_configuration_v01.sh`

升级为配置驱动的多仓库流程：repo manifest 发现、TOML 配置、语言探测、解析器能力矩阵、strict 模式。

### `installers/install_vendor_customization_graph_v01.sh`

闭源厂商定制图谱融合流水线：自动调用 `jadx` 反编译 `.jar`/`.apk`，将反编译源码无缝增量融合到 AOSP 基线图谱。

### `installers/install_permission_enforcement_graph_v01.sh`

增加权限执行图谱（Phase 2a）：通过正则扫描源码与 XML 清单，将 `@RequiresPermission`、`checkPermission` 控制流精确映射到 `METHOD`，形成 `ENFORCES_PERMISSION` 边。

## 四、环境要求

```text
WSL2 Ubuntu 24.04
AOSP: /home/ts/aosp
工具工程: /home/ts/android-context-intelligence
```

需要提前安装：

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  git universal-ctags sqlite3 ripgrep jq graphviz default-jre-headless
```

厂商反编译还需要 `jadx`（放入 WSL 原生目录以避免跨文件系统 I/O 瓶颈）。

## 五、快速开始

### 1. 复制到 WSL

```bash
mkdir -p /home/ts/android-context-installers
cp /mnt/d/AndroidContextIntelligence/setup.sh /home/ts/android-context-installers/
cp -r /mnt/d/AndroidContextIntelligence/installers /home/ts/android-context-installers/
chmod +x /home/ts/android-context-installers/setup.sh
chmod +x /home/ts/android-context-installers/installers/*.sh
```

### 2. 一键安装

```bash
cd /home/ts/android-context-installers
./setup.sh --fresh
```

### 3. 自定义路径

```bash
AOSP_ROOT=/path/to/aosp \
PROJECT_ROOT=/path/to/project \
./setup.sh --fresh
```

### 4. 重装/更新

```bash
./setup.sh --rebuild
```

## 六、日常使用

安装完成后，日常操作在工具工程内进行：

```bash
cd /home/ts/android-context-intelligence
```

### AOSP 基线图谱重建

```bash
./scripts/rebuild_all.sh
```

### 厂商闭源包融合

```bash
# 1. 投放厂商包
cp services.jar framework.jar SystemUI.apk data/raw/vendor/

# 2. 一键反编译 + 图谱融合
./scripts/import_vendor.sh
```

融合机制说明：
- **同名更新（Shadowing）**：厂商直接修改的 AOSP 类会覆盖原节点属性，源码路径指向反编译目录。
- **增量嫁接（Grafting）**：厂商新增的子类会通过 `EXTENDS` 边自动桥接到 AOSP 基类节点。

### 其他常用命令

| 命令 | 说明 |
|------|------|
| `./scripts/rebuild_all.sh --discover-only` | 只发现仓库和语言，不修改数据库 |
| `./scripts/rebuild_all.sh --plan-only` | 只生成执行计划 |
| `./scripts/rebuild_all.sh --strict` | 严格模式，有能力缺口则失败 |
| `./scripts/rebuild_all.sh --keep-failed-db` | 保留失败批次用于排查 |

## 七、查询图谱

数据库位置：`data/android_context.db`

### 常用查询示例

```sql
-- 节点类型分布
SELECT node_type, COUNT(*) FROM node GROUP BY node_type ORDER BY COUNT(*) DESC;

-- 边类型分布
SELECT edge_type, COUNT(*) FROM edge GROUP BY edge_type ORDER BY COUNT(*) DESC;

-- 查找厂商定制类
SELECT display_name, qualified_name, source_path
FROM node
WHERE source_path LIKE '%vendor_src%' AND node_type LIKE '%CLASS%'
LIMIT 20;

-- AMS 的继承链
SELECT n2.qualified_name AS parent, e.edge_type
FROM edge e
JOIN node n1 ON e.from_node_id = n1.node_id
JOIN node n2 ON e.to_node_id = n2.node_id
WHERE n1.qualified_name LIKE '%ActivityManagerService%'
  AND e.edge_type IN ('EXTENDS', 'IMPLEMENTS_BINDER');

-- 某个核心类的所有方法
SELECT m.display_name, m.source_path, m.line_start
FROM edge e
JOIN node c ON e.from_node_id = c.node_id
JOIN node m ON e.to_node_id = m.node_id
WHERE c.display_name = 'ActivityStarter'
  AND e.edge_type = 'HAS_METHOD';
```

### 预置查询脚本

```bash
sqlite3 -header -column data/android_context.db < queries/ams_service_chain.sql
sqlite3 -header -column data/android_context.db < queries/pms_service_chain.sql
sqlite3 -header -column data/android_context.db < queries/local_services_summary.sql
```

### 分享给他人

`android_context.db` 是自包含的 SQLite 文件，可以直接分享：
- 接收方无需安装 WSL 或任何构建工具，任何平台的 SQLite 客户端即可查询。
- 如需接收方也能查看反编译源码，需同时提供 `data/staging/vendor_src/` 目录。

## 八、增加其他仓库

编辑 `config/source_roots.toml`：

```toml
# 启用 Permission Mainline 模块
[repositories."packages/modules/Permission"]
enabled = true
languages = ["java", "kotlin", "aidl"]

# 限制扫描目录
[repositories."vendor/example"]
enabled = true
include = ["framework", "service", "interfaces"]
exclude = ["tests", "prebuilt", "generated"]
```

## 九、主要输出产物

| 路径 | 说明 |
|------|------|
| `data/android_context.db` | 核心图谱数据库 |
| `data/workspace/repositories.json` | 仓库发现结果 |
| `data/workspace/capability-report.json` | 解析器能力报告 |
| `data/workspace/execution-plan.json` | 执行计划 |
| `data/raw/ctags/<repo>.jsonl` | 原始 Ctags 数据 |
| `data/raw/inheritance/<repo>.json` | 继承关系报告 |
| `data/raw/service/service-registration-report.json` | 服务注册报告 |
| `data/staging/vendor_src/` | 厂商反编译源码 |

## 十、当前限制

1. **不是完整编译器**：基于 Ctags 启发式解析，对匿名类、复杂泛型、编译期条件分支等可能无法完全解析。
2. **Kotlin 继承关系不完整**：Ctags 尚不支持 Kotlin 的 `inherits` 字段，Kotlin 类的继承边暂缺。
3. **无方法体内调用图**：图谱存储类级骨架（方法签名、继承、Binder），不存储方法体内部的 `CALLS` 关系。
4. **无 Build Graph**：尚不能回答 `Source → Soong Module → Artifact → Partition` 的编译链路。
5. **无 Runtime Graph**：不包含设备实时状态（运行中的 Service、UID、SELinux denial 等）。

## 十一、故障排查

| 问题 | 解决方案 |
|------|---------|
| `Missing AOSP root` | `ls /home/ts/aosp/frameworks/base` 确认路径，或 `AOSP_ROOT=/path ./setup.sh --fresh` |
| Ctags 版本不对 | `ctags --version` 输出必须包含 `Universal Ctags` |
| Jadx 反编译静默失败 | 确认 `jadx` 在 WSL 原生目录，用 `jadx --version` 验证；检查 JRE 是否安装 |
| WSL 跨文件系统慢 | 所有重 I/O 操作必须在 `/home/` 下执行，避免 `/mnt/d/`（9P 协议瓶颈） |
| 外键检查 | `sqlite3 data/android_context.db 'PRAGMA foreign_key_check;'`，无输出表示完整 |

## 十二、技术路线图

完整架构设计见 [android-specific-context-graph.md](doc/architecture/android-specific-context-graph.md)，详细技术方案见 [Final Technical Plan](doc/architecture/Android_Context_Graph_Final_Technical_Plan.md)。

以下是架构文档定义的 7 阶段路线与当前实际进度的对照：

| 阶段 | 架构定义 | 当前状态 | 说明 |
|------|---------|---------|------|
| **Phase 0** | 基础环境 | ✅ 已完成 | WSL2 + SQLite + Universal Ctags + Graph Schema |
| **Phase 1** | Generic Code Graph | ✅ 已完成 | Java/Kotlin Symbol 743K+、AIDL/Binder 885 接口、Inheritance 9K+ 边、Service Registration 90 服务 |
| — | Multi-Repository | ✅ 已完成 | 1087 仓库发现、TOML 配置驱动、原子化重建 |
| — | Vendor Customization | ✅ 已完成 | Jadx 反编译 + 增量融合，2M+ 厂商节点 |
| **Phase 2** | Android Semantic Graph | 🔜 **下一步** | Permission Graph、Build Graph（Soong/Ninja） |
| **Phase 3** | Incremental Updater | 🔲 未开始 | Git Change Detector、Graph Patch、Stale 标记 |
| **Phase 4** | Runtime / Test Graph | 🔲 未开始 | ADB/Perfetto 采集、CTS/XTS 结果解析、PASS/FAIL Diff |
| **Phase 5** | Context Expander | 🔲 未开始 | Issue Parser → Seed Nodes → Rule-based Expansion → Problem Context Graph |
| **Phase 6** | Loop Engine | 🔲 未开始 | Observe → Expand → Diagnose → Plan → Execute → Verify 闭环 |

### Phase 2 细分计划（下一步行动）

根据 [Final Technical Plan](doc/architecture/Android_Context_Graph_Final_Technical_Plan.md) 的优先级建议，Phase 2 将按以下顺序实施：

**2a. Permission Enforcement Graph**
- 扫描 `checkPermission()`、`enforceCallingOrSelfPermission()`、`@RequiresPermission`
- 解析 `AndroidManifest.xml` 权限声明、`privapp-permissions*.xml`、`sysconfig/*.xml`
- 生成 `ENFORCES_PERMISSION`、`REQUIRES_PERMISSION`、`GRANTED_BY` 等边

**2b. Build Graph**
- 解析 Soong module graph / actions + Ninja 依赖
- 建立 `Source → Soong Module → Artifact → Partition → Image` 链路
- 回答"修改某文件需要重建哪些模块"

**2c. AI-MCP 集成（可与 2a/2b 并行）**
- 为大语言模型提供标准化图谱查询接口（MCP）
- 结合 `jadx-ai-mcp` 实现"图谱宏观导航 + 反编译微观透视"

### 关于高级分析引擎 (CodeQL / SCIP / Joern) 的演进说明

在早期架构规划中，CodeQL / SCIP / Joern 曾被列为图谱构建的主力引擎。但在实际落地（[Final Technical Plan](doc/architecture/Android_Context_Graph_Final_Technical_Plan.md)）中，我们做出了关键调整：**它们不再是基础图谱的必需组件，而是后续按需引入的“增强层”**。

在 Phase 0-1 阶段，我们采用 `Universal Ctags + 启发式 Python 解析器` 作为轻量级平替，在不编译 AOSP 的前提下，以极低成本完成了 90% 的关键骨架提取（类/方法/Binder/系统服务）。

在后续阶段，当启发式解析无法满足精度时，我们将按需引入这些重型引擎：
- **CodeQL**：用于精确的方法内 Call Graph、数据流追踪（Data Flow），特别是追踪权限检查沿调用栈的跨组件传播。
- **SCIP**：用于大规模跨仓的精确 Definition / Reference 索引。
- **Joern**：用于 C/C++、HAL 和 Native Binder 链路的安全与逻辑分析。

## 十三、`android-context-current` 说明

该目录是之前 WSL 工程的开发快照，用于安装脚本的验证基线。干净安装**不需要复制**此目录。仅在以下场景有用：检查已生成工程的源码、对比安装脚本生成结果、在没有 WSL 环境时进行接口审查。

## 十四、相关文档

- [文档索引](doc/README.md)
- [总体架构](doc/architecture/android-specific-context-graph.md)
- [技术方案](doc/architecture/Android_Context_Graph_Final_Technical_Plan.md)
- [Kotlin 与 Vendor 可行性分析](doc/feasibility/kotlin_parser_and_vendor_extraction.md)
