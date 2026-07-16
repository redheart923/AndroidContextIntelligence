# Documentation Index

仓库根目录的 `README.md` 是安装和使用入口。其余项目文档按职责归档在本目录。

## Architecture

- [Android-specific Context Graph](architecture/android-specific-context-graph.md)：总体目标、分层模型、技术选型、语义图谱和长期路线图。

## Designs

- [Atomic Database Rebuild v0.1 Design](designs/2026-07-16-atomic-database-rebuild-v01-design.md)：原子重建、失败回滚、WAL 安全和发布恢复设计。

## Plans

- [Multi-Repository Source Configuration v0.1 Plan](plans/2026-07-16-multi-repository-source-configuration-v01-plan.md)：已完成的多仓库配置实施与验收记录。
- [Atomic Database Rebuild v0.1 Plan](plans/2026-07-16-atomic-database-rebuild-v01-plan.md)：当前原子数据库重建 TDD 实施计划。

新增文档时继续使用以下分类：

```text
doc/architecture/  长期架构和系统模型
doc/designs/       已确认的功能设计
doc/plans/         可执行实施计划和验收记录
```
