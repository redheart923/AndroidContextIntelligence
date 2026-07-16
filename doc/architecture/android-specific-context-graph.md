# Android\-specific Context Graph

# **Android\-specific Context Graph 技术选型与落地方案**



## **1\. 项目目标**



构建一个面向 Android 系统开发的 **Android System Intelligence Platform**，用于：



- 基于 AOSP 源码、Build、Runtime、Test 数据生成 Android Context Graph。

- 支撑 CTS/XTS、ANR、Tombstone、SELinux、Framework 修改等问题分析。

- 为 AI Agent 提供可信、可追踪、可验证的上下文。

- 实现 `Diagnose → Patch → Build → Verify` 闭环。

- 将 Android 系统工程师的隐性经验结构化为可查询的机器知识。

核心原则：



> 基础知识图谱由确定性工具生成；AI 不负责创造事实，只负责基于事实进行问题理解、推理、规划与闭环修复。
> 
> 



**\-\-\-**



## **2\. 总体架构**



```Plain Text
AOSP Source / Vendor Source / Test Data / Device Runtime
                         |
                         v
+--------------------------------------------------------+
|                 Deterministic Fact Layer               |
| CodeQL / SCIP / Joern / Tree-sitter / Soong / ADB     |
+--------------------------------------------------------+
                         |
                         v
                 Android Base Context Graph
                         |
                         v
                  Context Router / Expander
                         |
                         v
                    AI Reasoning Layer
                         |
                         v
                     Loop Engine
                         |
               +---------+---------+
               |                   |
               v                   v
           Executor             Verifier
      grep/build/adb/flash   build/boot/CTS/XTS
                         |
                         v
              Trace / Audit / Hubble-lite
```



**\-\-\-**



## **3\. 分层模型**



### **3\.1 Fact Layer**



由确定性工具生成，例如：



```Plain Text
ActivityManagerService IMPLEMENTS IActivityManager
ActivityManagerService BUILT_INTO services.jar
services.jar PACKAGED_IN system.img
```



### **3\.2 Context Layer**



把事实组织成 Android 语义关系，例如：



```Plain Text
API
  -> Binder Interface
  -> System Service
  -> Permission Check
  -> XML Grant
  -> SELinux Domain
```



### **3\.3 Reasoning Layer**



AI 基于事实和关系完成：



- Root Cause 候选生成

- 证据排序

- 下一步采集计划

- Patch 建议

- 验证路径规划

**\-\-\-**



## **4\. 技术选型**



### **4\.1 CodeQL**



用途：



- Java/Kotlin/C/C\+\+ 语义分析

- AST、Call Graph、Data Flow、Type Relation

- Binder 方法与权限检查模式查询

- Framework 影响分析

适合：



- `frameworks/base`

- `packages/modules`

- `system/core`

定位：



> 主要的语义分析引擎。
> 
> 



**\-\-\-**



### **4\.2 SCIP**



用途：



- 大规模 Symbol Index

- Definition / Reference 查询

- 跨仓符号跳转

- 修改影响范围快速检索

适合：



- AOSP 多仓大规模索引

- Context Expander 的快速符号定位

定位：



> 大规模代码导航和引用索引底座。
> 
> 



**\-\-\-**



### **4\.3 Joern**



用途：



- Code Property Graph

- AST \+ CFG \+ DFG \+ Call Graph

- Native Service、HAL、Daemon、Vendor C/C\+\+ 分析

适合：



- `system/core`

- `frameworks/av`

- `hardware/interfaces`

- `vendor/`

- Native Binder / HAL 链路

定位：



> Native 和安全分析引擎。
> 
> 



**\-\-\-**



### **4\.4 Tree\-sitter**



用途：



- 统一解析多种源码与配置格式

- 补充 CodeQL / SCIP / Joern 不直接覆盖的 Android 文件

重点支持：



- Java / Kotlin / C / C\+\+

- AIDL

- Android\.bp / Android\.mk

- XML

- SELinux Policy

- VINTF XML

定位：



> Android 特有格式解析器底座。
> 
> 



**\-\-\-**



### **4\.5 Graph Storage**



推荐顺序：



1\. **MVP：SQLite \+ NetworkX**

2\. **多人协作与图查询：Neo4j / Memgraph**

3\. **已有 PostgreSQL 体系：PostgreSQL \+ Apache AGE**



第一阶段不建议过早引入复杂分布式图数据库。



**\-\-\-**



### **4\.6 Agent 与工作流**



推荐：



- MVP：自研有限状态机

- 中期：LangGraph 或自研 DAG Runtime

- Trace：OpenTelemetry \+ JSONL

- 队列：Python Queue；规模扩大后再引入 Kafka / RabbitMQ

不建议第一版就做多 Agent Debate。



**\-\-\-**



## **5\. Android Semantic Layer**



通用代码图谱只能回答“谁调用谁”，不能回答“为什么这个 Android 行为会发生”。因此必须增加 Android 领域语义。



### **5\.1 Service Graph**



数据来源：



- `SystemServer.java`

- `SystemService`

- `ServiceManager.addService()`

- `publishBinderService()`

- `LocalServices.addService()`

关系示例：



```Plain Text
SystemServer
  STARTS
ActivityManagerService

ActivityManagerService
  PUBLISHES_BINDER
activity

ActivityManagerService
  IMPLEMENTS
IActivityManager
```



**\-\-\-**



### **5\.2 Binder Graph**



数据来源：



- `*.aidl`

- `Stub` / `Proxy`

- ServiceManager 注册

- Binder Client 获取逻辑

关系示例：



```Plain Text
Manager API
  CALLS
AIDL Method
  IMPLEMENTED_BY
System Service Method
```



重点记录：



- Interface

- Method

- Transaction

- Caller

- Server

- Thread / Process

- Permission Check

**\-\-\-**



### **5\.3 Permission Graph**



数据来源：



- `frameworks/base/core/res/AndroidManifest.xml`

- `privapp-permissions*.xml`

- `sysconfig/*.xml`

- AppOps

- PermissionManagerService

- `enforceCallingPermission` / `checkPermission`

关系示例：



```Plain Text
API
  REQUIRES_PERMISSION
android.permission.X

android.permission.X
  DECLARED_IN
AndroidManifest.xml

Package
  GRANTED_BY
privapp-permissions.xml
```



**\-\-\-**



### **5\.4 Build Graph**



数据来源：



- `Android.bp`

- `Android.mk`

- Soong

- Ninja

- 产品配置和安装清单

关系示例：



```Plain Text
Source File
  BELONGS_TO_MODULE
framework-services

framework-services
  PRODUCES
services.jar

services.jar
  INSTALLED_IN
system/framework

system/framework
  PACKAGED_IN
system.img
```



该图用于回答：



- 修改某文件会影响哪个模块？

- 最终进入哪个 Jar / APEX / Partition / Image？

- 为什么替换 Jar 与替换整张 system\.img 结果不同？

**\-\-\-**



### **5\.5 SELinux Graph**



数据来源：



- `system/sepolicy`

- `vendor/*/sepolicy`

- `file_contexts`

- `service_contexts`

- AVC denial

- neverallow

关系示例：



```Plain Text
Process
  RUNS_IN_DOMAIN
system_server

system_server
  ALLOWED
camera_device:chr_file read
```



**\-\-\-**



### **5\.6 APEX / Mainline Graph**



数据来源：



- APEX Manifest

- Soong APEX Module

- `/apex`

- `apexservice list`

关系示例：



```Plain Text
Module
  PACKAGED_IN
com.android.permission.apex

APEX
  ACTIVE_VERSION
123456
```



**\-\-\-**



### **5\.7 VINTF / HAL Graph**



数据来源：



- `manifest.xml`

- `compatibility_matrix.xml`

- HIDL / AIDL HAL 定义

- `lshal`

关系示例：



```Plain Text
Framework Requirement
  REQUIRES_HAL
android.hardware.camera.provider

Device Manifest
  PROVIDES_VERSION
2.7
```



**\-\-\-**



### **5\.8 Test Graph**



数据来源：



- CTS / VTS / GTS / XTS source

- `test_result.xml`

- Test module metadata

- Failure log

关系示例：



```Plain Text
Test Case
  COVERS
Framework API

Test Case
  FAILED_ON
Build Fingerprint

Failure
  REFERENCES
System Service / Permission / HAL
```



**\-\-\-**



## **6\. Runtime Graph**



源码图描述“理论上是什么”，Runtime Graph 描述“设备现在是什么”。



### **6\.1 采集项**



```Bash
adb shell getprop
adb shell service list
adb shell dumpsys package
adb shell dumpsys activity
adb shell dumpsys permission
adb shell apexservice list
adb shell lshal
adb shell logcat
adb shell dmesg
```



可选：



- Perfetto

- Binder trace

- atrace

- simpleperf

- tombstone

- ANR traces

### **6\.2 Runtime 图谱内容**



- 实际 Service 注册状态

- Package / UID / Signature

- Permission Grant

- Active APEX Version

- HAL 实际实现

- SELinux Denial

- 当前 Fingerprint / SPL

- Process / PID / Domain

- Runtime Call / Trace

**\-\-\-**



## **7\. Context Expander**



Context Expander 不创建事实，而是从完整图谱中选择和扩展问题相关子图。



输入：



- CTS/XTS Fail

- Exception

- Stack Trace

- ANR

- Tombstone

- SELinux Denial

- Framework 类名

- Service 名

- 功能描述

处理流程：



```Plain Text
Issue Parser
   |
Seed Nodes
   |
Rule-based Expansion
   |
Rank / Prune
   |
Problem Context Graph
   |
Structured Context Package
```



示例：



```Plain Text
SecurityException
  -> API
  -> Binder Interface
  -> System Service
  -> Permission
  -> XML Grant
  -> AppOps
  -> SELinux
```



推荐输出格式：



```JSON
{
  "issue": {},
  "seed_nodes": [],
  "related_services": [],
  "code_paths": [],
  "build_artifacts": [],
  "permissions": [],
  "runtime_facts": [],
  "test_evidence": [],
  "pass_fail_diffs": [],
  "missing_evidence": []
}
```



**\-\-\-**



## **8\. Loop Engine**



第一版采用单 Reasoner \+ 强 Verifier，而不是多 Agent 讨论。



```Plain Text
OBSERVE
  -> EXPAND_CONTEXT
  -> DIAGNOSE
  -> PLAN
  -> EXECUTE
  -> VERIFY
  -> COLLECT_DELTA
  -> RETRY / DONE
```



### **8\.1 Reasoner**



负责：



- 生成 Root Cause 假设

- 引用图谱证据

- 判断缺失证据

- 生成下一步计划

### **8\.2 Executor**



负责：



- grep / CodeQL Query / Graph Query

- build

- adb

- pull / push

- flash

- CTS/XTS

- Patch 应用

### **8\.3 Verifier**



必须尽量确定性：



- Build 是否通过

- Device 是否启动

- Test 是否通过

- 是否新增 crash / ANR

- 是否新增 AVC denial

- 是否引入回归

原则：



> Agent 不允许自行宣布修复成功，必须由 Verifier 提供外部结果。
> 
> 



**\-\-\-**



## **9\. 推荐项目目录结构**



```Plain Text
android-context-intelligence/
├── README.md
├── pyproject.toml
├── Makefile
├── docker-compose.yml
│
├── configs/
│   ├── graph_schema.yaml
│   ├── analyzers.yaml
│   ├── android_versions/
│   │   ├── android14.yaml
│   │   ├── android15.yaml
│   │   └── android16.yaml
│   └── device_profiles/
│
├── docs/
│   ├── architecture.md
│   ├── graph-schema.md
│   ├── update-strategy.md
│   └── query-examples.md
│
├── data/
│   ├── raw/
│   ├── normalized/
│   ├── snapshots/
│   ├── patches/
│   └── traces/
│
├── collectors/
│   ├── source/
│   │   ├── repo_scanner.py
│   │   └── file_indexer.py
│   ├── build/
│   │   ├── android_bp_parser.py
│   │   ├── android_mk_parser.py
│   │   ├── soong_collector.py
│   │   └── ninja_collector.py
│   ├── binder/
│   │   ├── aidl_parser.py
│   │   └── service_registration_scanner.py
│   ├── permission/
│   │   ├── manifest_parser.py
│   │   ├── privapp_parser.py
│   │   ├── sysconfig_parser.py
│   │   └── appops_parser.py
│   ├── sepolicy/
│   │   ├── policy_parser.py
│   │   └── avc_parser.py
│   ├── vintf/
│   │   └── vintf_parser.py
│   ├── test/
│   │   ├── cts_result_parser.py
│   │   └── xts_result_parser.py
│   └── runtime/
│       ├── adb_collector.py
│       ├── dumpsys_collector.py
│       ├── apex_collector.py
│       ├── logcat_collector.py
│       └── perfetto_collector.py
│
├── analyzers/
│   ├── code/
│   │   ├── codeql_adapter.py
│   │   ├── scip_adapter.py
│   │   └── joern_adapter.py
│   └── android/
│       ├── service_analyzer.py
│       ├── binder_analyzer.py
│       ├── permission_analyzer.py
│       ├── build_analyzer.py
│       ├── apex_analyzer.py
│       ├── sepolicy_analyzer.py
│       ├── vintf_analyzer.py
│       └── test_analyzer.py
│
├── graph/
│   ├── schema/
│   │   ├── node_types.py
│   │   ├── edge_types.py
│   │   └── constraints.py
│   ├── models.py
│   ├── builder.py
│   ├── merger.py
│   ├── validator.py
│   ├── differ.py
│   └── serializer.py
│
├── storage/
│   ├── base.py
│   ├── sqlite_store.py
│   ├── neo4j_store.py
│   ├── migrations/
│   └── queries/
│
├── updater/
│   ├── change_detector.py
│   ├── dependency_tracker.py
│   ├── update_planner.py
│   ├── incremental_builder.py
│   ├── graph_patch.py
│   ├── snapshot_manager.py
│   └── version_manager.py
│
├── context/
│   ├── issue_parser.py
│   ├── seed_resolver.py
│   ├── router.py
│   ├── expander.py
│   ├── ranker.py
│   ├── pruner.py
│   └── package_builder.py
│
├── loop/
│   ├── state_machine.py
│   ├── reasoner.py
│   ├── executor.py
│   ├── verifier.py
│   ├── policy_engine.py
│   └── retry_policy.py
│
├── tools/
│   ├── adb_tools.py
│   ├── build_tools.py
│   ├── test_tools.py
│   ├── graph_tools.py
│   └── code_search_tools.py
│
├── trace/
│   ├── events.py
│   ├── jsonl_exporter.py
│   └── otel_exporter.py
│
├── cli/
│   ├── main.py
│   ├── build_graph.py
│   ├── update_graph.py
│   ├── query_graph.py
│   ├── expand_context.py
│   └── diagnose.py
│
└── tests/
    ├── unit/
    ├── integration/
    ├── fixtures/
    └── benchmark/
```



**\-\-\-**



## **10\. 知识图谱更新策略**



知识图谱应被视为 Android 源码和设备状态的“持续编译产物”，而不是人工维护的静态数据库。



### **10\.1 总体更新流程**



```Plain Text
Source / Config / Runtime / Test Change
                  |
                  v
           Change Detector
                  |
                  v
          Dependency Tracker
                  |
                  v
            Update Planner
                  |
                  v
        Incremental Re-analysis
                  |
                  v
             Graph Patch
                  |
                  v
        Validate -> Commit Snapshot
```



**\-\-\-**



### **10\.2 源码增量更新**



输入：



```Bash
git diff --name-status <old_commit> <new_commit>
```



根据文件类型路由：



- `.java` / `.kt`：CodeQL / SCIP

- `.cpp` / `.c`：Joern / CodeQL

- `.aidl`：Binder Analyzer

- `Android.bp` / `Android.mk`：Build Analyzer

- `*.xml`：Permission / VINTF Analyzer

- `*.te` / `file_contexts`：SELinux Analyzer

只重建受影响子图。



例：



```Plain Text
ActivityManagerService.java changed
  -> Rebuild Class / Method edges
  -> Rebuild Binder mapping
  -> Rebuild Build impact
  -> Mark related Test edges stale
```



**\-\-\-**



### **10\.3 构建图更新**



当以下内容发生变化时更新：



- Android\.bp

- Android\.mk

- Product Makefile

- APEX 定义

- Soong 配置

- Ninja Graph

重算：



```Plain Text
Source -> Module -> Artifact -> Partition -> Image
```



**\-\-\-**



### **10\.4 Runtime 更新**



Static Graph 与 Runtime Graph 必须分离。



Runtime 数据按设备和 Build 保存：



```Plain Text
runtime_snapshot_id
device_serial
build_fingerprint
security_patch
timestamp
```



触发方式：



- 每次刷机后

- 每次连接测试设备后

- 每次 CTS/XTS 执行前后

- 关键故障复现时

Runtime 更新不覆盖静态图，而是建立新的 Runtime Snapshot。



**\-\-\-**



### **10\.5 Test Graph 更新**



每次测试运行生成：



```Plain Text
TestRun
  -> Build
  -> Device
  -> TestCase
  -> Result
  -> Failure
  -> Logs
```



失败问题可与 Service、Permission、APEX、HAL 等节点建立证据边。



**\-\-\-**



### **10\.6 Android 版本更新**



Android 14、15、16 不应互相覆盖。



每个节点和边至少携带：



```Plain Text
source_revision
android_version
product
build_variant
device
valid_from
valid_to
```



支持：



```Plain Text
AOSP android14_rXX
        VS
AOSP android15_rXX
```



做跨版本 Graph Diff。



**\-\-\-**



### **10\.7 Graph Patch**



每次更新不直接大范围覆写数据库，先生成 Patch：



```JSON
{
  "base_revision": "abc123",
  "target_revision": "def456",
  "add_nodes": [],
  "update_nodes": [],
  "remove_nodes": [],
  "add_edges": [],
  "remove_edges": [],
  "invalidate_edges": []
}
```



通过 Schema Validator 和一致性检查后再提交。



**\-\-\-**



### **10\.8 Stale 标记**



当无法立即完成重算时，不删除旧数据，而是标记：



```Plain Text
status = stale
stale_reason = source_changed
stale_since = def456
```



Context Expander 默认降低 stale 事实的置信度，避免 Agent 使用过期关系。



**\-\-\-**



### **10\.9 全量重建时机**



以下情况建议全量重建：



- Graph Schema 大版本升级

- CodeQL / SCIP / Joern 索引格式变化

- Android 大版本切换

- Soong / Build 规则发生大规模变动

- 增量校验发现图谱不一致

- 定期夜间重建用于校验增量结果

原则：



> 日常使用增量更新；周期性全量重建用于校准。
> 
> 



**\-\-\-**



## **11\. 版本和快照设计**



推荐同时维护三类版本：



### **Source Revision**



```Plain Text
repo manifest revision + per-project commit
```



### **Graph Revision**



```Plain Text
graph_schema_version + graph_build_id
```



### **Runtime Snapshot**



```Plain Text
device + fingerprint + timestamp
```



查询必须显式指定：



```Plain Text
Source Graph Version
Runtime Snapshot
Test Run
```



避免不同版本事实混在一起。



**\-\-\-**



## **12\. 数据质量与验证**



基础图谱不应依赖 AI 做事实验证。



自动验证规则：



- Service 必须能关联注册点或来源证据

- Binder Interface 必须有 AIDL / Stub 证据

- Build Artifact 必须有 Soong / Ninja 证据

- Permission 必须有声明源

- Runtime Service 必须有设备快照

- Edge 必须保存 provenance

- 每条事实必须能追溯到文件、行号、命令或日志

推荐每个节点/边保存：



```Plain Text
source_type
source_path
source_revision
line_start
line_end
extractor
extractor_version
confidence
timestamp
```



其中确定性提取的 `confidence` 通常为 1\.0；启发式匹配必须降低置信度。



**\-\-\-**



## **13\. MVP 实施路线**



### **Phase 0：基础环境（1～2 周）**



完成：



- AOSP 源码准备

- CodeQL / SCIP / Joern 安装验证

- SQLite / NetworkX 存储

- Graph Schema v0\.1

### **Phase 1：Generic Code Graph（3～4 周）**



覆盖：



- Class

- Method

- Definition

- Reference

- Call

- Inheritance

范围：



- `frameworks/base/services`

- `frameworks/base/core`

### **Phase 2：Android Semantic Graph（4～6 周）**



优先实现：



1. Service Graph

2. Binder Graph

3. Permission Graph

4. Build Graph

首批服务：



- ActivityManagerService

- PackageManagerService

- PermissionManagerService

- WindowManagerService

### **Phase 3：Updater（3～4 周）**



完成：



- Git Change Detector

- File Type Router

- Incremental Builder

- Graph Patch

- Snapshot Manager

- Stale 标记

### **Phase 4：Runtime / Test Graph（3～4 周）**



完成：



- adb collector

- dumpsys / APEX / VINTF collector

- CTS/XTS result parser

- PASS / FAIL Snapshot Diff

### **Phase 5：Context Expander（3～4 周）**



输入：



- Test 名

- Stack Trace

- Exception

- Service / Class

输出：



- Problem Context Graph

- Structured Context Package

- Missing Evidence

### **Phase 6：Loop Engine（4～6 周）**



先实现只读诊断闭环：



```Plain Text
Observe -> Expand -> Diagnose -> Collect Evidence -> Verify
```



稳定后再增加自动 Patch、Build、Flash。



**\-\-\-**



## **14\. 第一个推荐落地点**



推荐从 **Permission / Binder / Build Context Graph** 开始。



第一个场景：



```Plain Text
system.img 替换后 XTS PASS，
仅替换 framework.jar / services.jar 后仍 FAIL。
```



输入：



- PASS / FAIL 设备 Runtime Snapshot

- XTS Test Result

- Framework / Services 源码版本

- Build Graph

输出：



- 相关 Service / Binder / Permission

- Jar / APEX / XML / SELinux / VINTF 差异

- Root Cause 候选

- 缺失证据

- 下一步采集命令

- 验证方案

**\-\-\-**



## **15\. 最终技术结论**



底层事实提取推荐：



```Plain Text
CodeQL
+ SCIP
+ Joern
+ Tree-sitter
+ Soong / Ninja Parser
+ ADB / Perfetto / CTS Collector
```



核心自研部分：



```Plain Text
Android Semantic Layer
+ Graph Schema
+ Incremental Updater
+ Context Router / Expander
+ Verifier-driven Loop Engine
+ Trace / Audit
```



真正的技术壁垒不是重新开发 AST 或普通代码图谱，而是：



> 把 Android 的 Service、Binder、Permission、Build、APEX、SELinux、VINTF、Runtime 和 Test 关系，转换成可追溯、可增量更新、可供 Agent 使用的系统上下文图谱。
> 
> 



