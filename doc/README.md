# Documentation Index

项目文档按职责分类归档在本目录。根目录的 [README.md](../README.md) 是安装和使用入口。

## Architecture

长期架构和系统模型。

- [Android-specific Context Graph](architecture/android-specific-context-graph.md)：总体目标、分层模型、技术选型、语义图谱和长期路线图。
- [Final Technical Plan](architecture/Android_Context_Graph_Final_Technical_Plan.md)：完整技术方案，包含节点/边设计、解析器策略和实施计划。

## Feasibility

可行性分析和技术验证。

- [可行性分析](feasibility/feasibility_analysis.md)：项目整体可行性评估。
- [Kotlin 解析与 Vendor 反编译方案](feasibility/kotlin_parser_and_vendor_extraction.md)：Kotlin 语法支持和 Jadx 厂商反编译集成的技术分析。

## Designs

已确认的功能设计。

- [Atomic Database Rebuild v0.1](designs/2026-07-16-atomic-database-rebuild-v01-design.md)：原子重建、失败回滚、WAL 安全和发布恢复设计。
- [Trustworthy Source and Installation Baseline](designs/2026-07-21-trustworthy-source-and-installation-baseline-design.md)：规范源码、薄安装器、WSL 生成物边界和可信发布基线设计。

## Plans

可执行实施计划和验收记录。

- [Multi-Repository Source Configuration v0.1](plans/2026-07-16-multi-repository-source-configuration-v01-plan.md)：已完成的多仓库配置实施与验收记录。
- [Atomic Database Rebuild v0.1](plans/2026-07-16-atomic-database-rebuild-v01-plan.md)：原子数据库重建 TDD 实施计划。
- [Trustworthy Source and Installation Baseline](plans/2026-07-21-trustworthy-source-and-installation-baseline-plan.md)：规范源码、确定性安装、漂移校验与 WSL 验收的分步实施计划。

## Reviews

基于代码、测试、WSL 部署和 live 数据的审查证据。

- [Repository Architecture Review](reviews/2026-07-21-repository-architecture-review.md)：重大风险、当前修复状态、数据库证据与后续优先级。
- [Canonical Source Drift Audit](reviews/2026-07-21-source-drift-audit.md)：从历史 WSL 生成目录迁移到 `project/` 时的逐项差异决策。
- [Trustworthy Baseline Acceptance](reviews/2026-07-21-trustworthy-baseline-acceptance.md)：WSL 临时 fresh/verify/upgrade 验收、漂移修复、保留路径和 live 数据库未改动证据。

## 文档分类规范

新增文档时继续使用以下分类：

```text
doc/architecture/  长期架构和系统模型
doc/feasibility/   可行性分析和技术验证
doc/designs/       已确认的功能设计
doc/plans/         可执行实施计划和验收记录
doc/reviews/       基于当前代码和运行状态的审查证据
```
