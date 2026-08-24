# Documentation Index

项目文档按职责分类归档在本目录。根目录的 [README.md](../README.md) 是安装和使用入口。

## Architecture

长期架构和系统模型。

- [Android-specific Context Graph](architecture/android-specific-context-graph.md)：总体目标、分层模型、技术选型、语义图谱和长期路线图。
- [Final Technical Plan](architecture/Android_Context_Graph_Final_Technical_Plan.md)：完整技术方案，包含节点/边设计、解析器策略和实施计划。
- [Internal Web Analysis Platform Extension](architecture/internal-web-analysis-platform-extension.md)：面向内部研发的 Web、REST/GraphQL 和证据化 AI 问答扩展；当前仅为路线图记录，尚未实施。

## Feasibility

可行性分析和技术验证。

- [可行性分析](feasibility/feasibility_analysis.md)：项目整体可行性评估。
- [Kotlin 解析与 Vendor 反编译方案](feasibility/kotlin_parser_and_vendor_extraction.md)：Kotlin 语法支持和 Jadx 厂商反编译集成的技术分析。

## Designs

已确认的功能设计。

- [Atomic Database Rebuild v0.1](designs/2026-07-16-atomic-database-rebuild-v01-design.md)：原子重建、失败回滚、WAL 安全和发布恢复设计。
- [Trustworthy Source and Installation Baseline](designs/2026-07-21-trustworthy-source-and-installation-baseline-design.md)：规范源码、薄安装器、WSL 生成物边界和可信发布基线设计。
- [Permission Semantics Graph v0.1](designs/2026-07-21-permission-semantics-graph-v01-design.md)：权限声明、请求、privapp/default-permissions 策略以及 Java/Kotlin 检查与执行语义设计。
- [Java/Kotlin Call and Interprocedural Dataflow Graph v0.1](designs/2026-08-20-java-kotlin-call-dataflow-graph-v01-design.md)：CodeQL 调用点、跨方法安全数据流、类型化存储、验证和 Git 纠错设计。
- [Partial Source Workspace Profile v0.1](designs/2026-08-24-partial-source-workspace-profile-v01-design.md)：局部源码的显式 scope、通用发布门禁、能力降级、provenance 和原子发布设计；尚未实施。

## Plans

Partial Source Workspace Profile v0.1 is implemented and accepted; see the review record
below for fixture, publication-integrity, and real WSL partial-source evidence.

- [Java/Kotlin Call and Interprocedural Dataflow Graph v0.1](plans/2026-08-20-java-kotlin-call-dataflow-graph-v01-plan.md): CodeQL database preparation, typed semantic storage, call/dataflow/security facts, corrections, atomic publication, and real-AOSP acceptance plan.
- [Partial Source Workspace Profile v0.1](plans/2026-08-24-partial-source-workspace-profile-v01-plan.md): scope identity, two-phase validation, provenance-bound publication, atomic failure gates, and partial-source acceptance plan.

可执行实施计划和验收记录。

- [Multi-Repository Source Configuration v0.1](plans/2026-07-16-multi-repository-source-configuration-v01-plan.md)：已完成的多仓库配置实施与验收记录。
- [Atomic Database Rebuild v0.1](plans/2026-07-16-atomic-database-rebuild-v01-plan.md)：原子数据库重建 TDD 实施计划。
- [Permission Enforcement Graph v0.1（已被设计取代）](plans/2026-07-17-permission-enforcement-graph-v01-plan.md)：旧版逐行扫描方案，仅保留为历史记录，不应继续执行。
- [Permission Semantics Graph v0.1](plans/2026-07-22-permission-semantics-graph-v01-plan.md)：已确认设计对应的分阶段 TDD 实施、原子发布验证和真实 AOSP 验收计划。
- [Trustworthy Multi-Source Ingestion v0.1](plans/2026-07-28-trustworthy-multi-source-ingestion-v01-plan.md)：能力质量、配置迁移、符号冲突、provenance、Vendor 原子导入与性能治理计划。
- [Trustworthy Source and Installation Baseline](plans/2026-07-21-trustworthy-source-and-installation-baseline-plan.md)：规范源码、确定性安装、漂移校验与 WSL 验收的分步实施计划。

## Reviews

- [Partial Source Workspace Profile v0.1 Acceptance](reviews/2026-08-24-partial-source-workspace-v01-acceptance.md): scope gates, publication integrity, deterministic fixtures, and real WSL partial-source smoke evidence.

基于代码、测试、WSL 部署和 live 数据的审查证据。

- [Repository Architecture Review](reviews/2026-07-21-repository-architecture-review.md)：重大风险、当前修复状态、数据库证据与后续优先级。
- [Canonical Source Drift Audit](reviews/2026-07-21-source-drift-audit.md)：从历史 WSL 生成目录迁移到 `project/` 时的逐项差异决策。
- [Trustworthy Baseline Acceptance](reviews/2026-07-21-trustworthy-baseline-acceptance.md)：WSL 临时 fresh/verify/upgrade 验收、漂移修复、保留路径和 live 数据库未改动证据。
- [Permission Semantics Graph v0.1 Acceptance](reviews/2026-07-22-permission-semantics-graph-v01-acceptance.md)：Permission 语义图的测试、真实 AOSP 重建、查询和确定性证据。
- [Post-Permission Repository Architecture Review](reviews/2026-07-28-post-permission-repository-architecture-review.md)：Permission 完成后的全仓库风险复核与下一阶段边界。
- [Trustworthy Multi-Source Ingestion v0.1 Acceptance](reviews/2026-08-04-trustworthy-multi-source-ingestion-v01-acceptance.md)：安装生命周期、真实 AOSP 双构建、Vendor/发布恢复、性能和仓库门禁证据。
- [Java/Kotlin Call and Dataflow Graph v0.1 Acceptance](reviews/2026-08-20-java-kotlin-call-dataflow-v01-acceptance.md)：CodeQL 数据库、真实 AOSP 强证据、纠错、原子发布与双构建指纹。

## 文档分类规范

新增文档时继续使用以下分类：

```text
doc/architecture/  长期架构和系统模型
doc/feasibility/   可行性分析和技术验证
doc/designs/       已确认的功能设计
doc/plans/         可执行实施计划和验收记录
doc/reviews/       基于当前代码和运行状态的审查证据
```
