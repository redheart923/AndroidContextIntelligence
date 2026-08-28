# Internal Web Analysis Platform Extension

Status: `ROADMAP / NOT IMPLEMENTED`

Recorded: 2026-08-21

## 1. Purpose

将 AndroidContextIntelligence 从离线、确定性的 Android 知识图谱构建器扩展为公司内网研发平台，使研发人员能够通过 Web、自然语言问答以及 REST/GraphQL API：

- 查询 Android Service、Binder、AIDL、权限、继承和调用关系；
- 浏览源码、调用链、权限路径和跨方法数据流；
- 学习项目、模块和系统架构；
- 分析代码修改的潜在影响范围；
- 比较不同项目、分支或图谱快照之间的架构差异；
- 获得带源码位置、图谱路径、提取器和置信状态的可审计回答。

本扩展不改变当前原则：确定性工具负责生成事实，AI 只基于已发布事实进行检索、解释和推理。

## 2. Confirmed Product Constraints

- 使用范围：公司内部研发人员。
- 预期规模：10–50 名用户，同时维护约 3–10 个项目或分支。
- 第一版入口：自然语言问答、图谱/源码浏览、REST API、GraphQL API 全部需要，但共享同一 Query Service，不能分别实现三套查询逻辑。
- 模型策略：混合模型。私有源码只能进入公司内网模型；公开 AOSP 内容可按策略进入批准的云模型。
- 身份策略：第一阶段尚不接公司 SSO，但必须预留反向代理身份头或最小 API Token、查询审计、项目边界和管理接口隔离。
- 更新方式：监测 Git/Repo 目标分支更新，异步构建新图谱，验证通过后原子发布并保留旧快照。
- 部署环境：尚未确定。应用必须使用可移植 Linux 容器，同一镜像可先运行在 Docker Compose，之后迁移到 Kubernetes/OpenShift。

## 3. Recommended Architecture

推荐保留现有 SQLite 构建产物，并增加 PostgreSQL 服务投影层：

```text
Git / Repo update
        |
        v
AndroidContextIntelligence Build Worker
        |
        | verified, fingerprinted, immutable
        v
SQLite Graph Snapshot + Reports
        |
        | publish projection
        v
PostgreSQL + pgvector
        |
        v
Unified Query Service
  |        |          |          |
  |        |          |          +-- Model Router / Evidence Builder
  |        |          +------------- GraphQL API
  |        +------------------------ REST API
  +--------------------------------- Web Application
```

### 3.1 Existing build plane

AndroidContextIntelligence 继续负责：

- Ctags、AIDL、Service、Permission 和 CodeQL 等离线分析；
- staged database build、验证、指纹、原子发布和回滚；
- 生成可归档、可复现的 SQLite 图谱快照及 provenance 报告。

构图进程与在线查询进程必须隔离。Web 请求不能直接触发写入 live 图谱，也不能绕过现有 publication gates。

### 3.2 Serving projection

PostgreSQL 保存在线服务所需的投影数据：

- project、repository、revision、snapshot 和 publication 状态；
- node、edge 及 CodeQL 类型化语义表的服务投影；
- 源码片段和符号检索文档；
- 用户查询、模型调用、证据引用和审计记录；
- 使用 pgvector 保存源码片段、类、方法和架构摘要的向量。

SQLite 是构建事实和归档边界，PostgreSQL 是在线查询模型。发布转换必须幂等，并通过 `snapshot_id` 隔离不同项目和分支。

### 3.3 Unified Query Service

建议使用 Python/FastAPI 实现单一 Query Service，提供：

- REST：稳定、任务导向的业务接口；
- GraphQL：图谱浏览器需要的按需字段和邻接关系查询；
- bounded graph traversal：限制深度、节点数、边类型和执行时间；
- hybrid retrieval：符号/全文/图路径/向量联合召回；
- evidence envelope：所有回答返回 snapshot、revision、source path、line、extractor、resolution status 和 graph path；
- model policy enforcement：依据源码分类选择内网模型或批准的云模型；
- audit：记录问题、检索范围、模型、证据和结果，不默认记录敏感源码全文。

前端、AI 问答和外部研发平台都必须调用该服务，不能直接访问 PostgreSQL 或 SQLite。

### 3.4 Web application

Web 应用包含三个共享上下文的工作区：

1. 问答：自然语言问题、证据列表、答案置信边界；
2. 图谱：按需展开节点和边，不将完整百万级图谱发送到浏览器；
3. 源码：文件、行号、符号详情、调用者/被调用者、权限和数据流侧栏。

用户在任一工作区选择项目、分支和快照后，其他工作区必须保持相同上下文。

## 4. Snapshot Publication Flow

```text
repository event
  -> queued build job
  -> isolated source checkout
  -> AndroidContextIntelligence rebuild
  -> graph and strong-evidence validation
  -> immutable artifact registration
  -> PostgreSQL projection into a non-live snapshot
  -> serving validation and smoke queries
  -> atomic active-snapshot switch
  -> retain previous snapshot for rollback and diff
```

构建失败、强证据失败或投影校验失败时，线上快照保持不变。

## 5. Model and Source-Security Policy

- 每个 repository/snapshot 必须标记 `private` 或 `public_aosp` 数据等级。
- 私有源码、私有图谱路径和内部问题只能发送给内网模型。
- 云模型请求必须经过服务端 Model Router，客户端不能直接持有云模型凭证。
- 混合回答中只要包含私有证据，整个生成步骤就切换到内网模型。
- 对外部模型调用保存策略决策、模型身份和证据哈希，但避免保存不必要的源码正文。
- AI 输出不得成为图谱事实；纠错仍通过现有 Git-managed corrections 和重新发布完成。

## 6. Alternatives Retained for Later Evaluation

### Direct SQLite serving

可用于短期 PoC，但不作为长期推荐方案。它最大化复用当前产物，却会增加多快照、向量、审计和并发服务管理难度。

### Neo4j serving layer

仅在 PostgreSQL 有界递归查询无法满足真实性能目标时评估。若引入 Neo4j，需要解决 PostgreSQL/Neo4j 双存储的一致性、快照切换、备份、高可用和许可成本；第一阶段不预先引入。

### SCIP interoperability

SCIP 仅在需要对接外部代码索引、IDE 或 Sourcegraph 类平台时评估，不作为第一阶段核心事实解析器。

## 7. Proposed Delivery Phases

本节仅记录建议顺序，不构成已批准实施计划。

1. Server foundation：项目、快照、任务、PostgreSQL 投影和只读 Query Service。
2. REST/GraphQL：符号查询、邻接展开、调用链、权限路径和源码定位。
3. Web explorer：项目/快照选择、图谱、源码和路径视图。
4. Evidence-grounded Q&A：混合检索、内网模型、模型策略和证据回答。
5. Git-triggered automation：构建队列、原子发布、回滚、通知和版本差异。
6. Identity hardening：接入公司 OIDC/LDAP，并落实项目级授权。
7. Measured scaling：根据生产指标决定是否引入 Neo4j、OpenSearch 或 Kubernetes。

## 8. Preconditions Before Implementation

实施前需要重新确认并形成正式设计：

- 目标服务器或容器平台；
- 公司允许使用的内网模型、云模型和数据分类规则；
- Git/Repo 事件来源和构建凭据；
- PostgreSQL 版本、备份、容量和高可用要求；
- 源码查看和审计数据的保留周期；
- REST/GraphQL 的首批查询用例和性能目标；
- 是否已有反向代理、API Gateway、OIDC 或 LDAP 基础设施。

## 9. Explicit Non-Implementation Boundary

记录本文件不会引入以下能力：

- 不新增 Web、FastAPI、GraphQL、PostgreSQL、pgvector 或模型依赖；
- 不创建数据库服务投影或迁移；
- 不新增容器、Compose 或 Kubernetes 部署文件；
- 不改变当前 SQLite schema、构图命令或 publication 流程；
- 不表示该服务器扩展已经设计完成、进入实施或通过验收。

只有在重新确认正式设计、写入实施计划并完成对应测试与验收后，状态才能从 `ROADMAP / NOT IMPLEMENTED` 更新。

## 10. Technical References

- [FastAPI in Containers](https://fastapi.tiangolo.com/deployment/docker/)
- [pgvector](https://github.com/pgvector/pgvector)
- [Neo4j Operations Manual](https://neo4j.com/docs/operations-manual/current/introduction/)
- [Neo4j Docker Compose deployment](https://neo4j.com/docs/operations-manual/current/docker/docker-compose-standalone/)

### 10.1 External code-graph reference projects

以下项目仅作为后续 MCP、查询服务、增量索引、IDE 集成、证据展示和图可视化的技术参考，不作为当前 Android Context Intelligence 权威事实来源，也不改变 Ctags、Tree-sitter、Blueprint、CodeQL、provenance、candidate isolation、correction replay、validation 和原子发布边界：

- [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)：重点参考持久化代码图、后台增量索引、覆盖度查询、MCP 工具面、多客户端共享和分级验证工作流。
- [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify)：重点参考 `EXTRACTED` / `INFERRED` / `AMBIGUOUS` 证据展示、路径解释、社区聚类、代码与文档联合图及静态 HTML 输出。
- [codegraph-ai/CodeGraph](https://github.com/codegraph-ai/CodeGraph)：重点参考 MCP/LSP/IDE 共用分析引擎、graph-only 模式、混合检索、PR impact analysis 和持久查询服务。

后续若进入实现，应先建立独立的 `MCP Query and External Graph Benchmark` 设计与验收计划，使用相同 AOSP fixture 比较 precision/recall、错误边、覆盖缺口、增量新鲜度、索引时间、数据库体积、查询延迟和 token 消耗。任何外部工具输出默认只能进入 external candidate/diagnostic 或对照报告；只有经过本项目身份解析、provenance 和 validation 后，才允许成为 active graph fact。
