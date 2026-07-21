# Android Context Intelligence Repository Architecture Review

## 审查结论

项目的长期方向可行，但审查时存在两个会阻断可信发布的架构问题：规范源码只存在于自解压脚本/WSL 生成目录，以及多个阶段覆盖同一完整文件。当前功能分支已经用受 Git 管控的 `project/` 和确定性薄安装器解决这两个基线问题。

Permission、Vendor 和 provenance 仍有高优先级正确性风险，不能因为相关文件或命令已经存在就标记为完成。

## 证据范围

审查使用了：

- Windows Git 仓库及其提交/工作树状态；
- WSL 的 `~/android-context-installers` 和 `~/android-context-intelligence`；
- WSL live SQLite 与 workspace 报告；
- `/home/ts/jadx-1.5.6/bin/jadx`；
- `/home/ts/jar-decompile/a17` 下的 A17 jar/apk 输入。

数字均是 2026-07-21 审查快照，不作为未来构建的固定指标。

## 重大风险

### P0 — 没有可审查的单一源码（本分支已修复）

审查前，关键 Python/SQL/TOML/测试仅嵌在 6 个 shell 的 heredoc/base64 中；WSL 生成工程又被当作人工同步基线。两份状态会漂移，也无法对普通源码做可靠 review。

修复证据：

- 完整可读实现已跟踪在 `project/`；
- 安装器只调用 `scripts/install_project.py`；
- 6 个旧阶段仅为兼容转发，不再写共享文件；
- `android-context-current/` 被排除，根测试不依赖 WSL 快照。

### P0 — 阶段覆盖与发布契约失效（本分支已修复）

旧阶段 1、4、6 都会重写 `scripts/rebuild_all.sh`，阶段 4/6 还会重写
`workspace/languages.py` 和 `config/parser_registry.toml`。最终正确性取决于
执行顺序和后续脚本是否复制了前面全部变更。

审查前根测试 4 项失败，规范项目 1 项失败。当前验证为根测试 44 项通过、规范项目 54 项通过；installer payload 由 SHA-256 manifest 校验。

### P1 — Permission Graph 调度和语义覆盖不足（未修复）

证据：

- 12,703 个 XML 文件被能力计划记录成 `symbols: unsupported`；planner 没有把 XML 的 `permission_declaration` 能力调度到 collector；
- live DB 只有 27 条 `REQUIRES_PERMISSION` 和 8 条 `ENFORCES_PERMISSION`，没有 XML permission declaration 边；
- XML importer 吞掉所有异常；
- Java/Kotlin 扫描器按单行正则和 `line_start` 关联方法，缺少 method end、常量表达式与多行语法。

影响：当前结果适合作为 PoC 信号，不能视为完整权限事实层。

### P1 — Vendor 导入绕过原子发布（未修复）

证据：

- `import_vendor.sh` 直接修改 live DB，没有共享 rebuild lock、staging batch 和 publication gates；
- 文档曾把输入放在 `data/raw/vendor`，但 `data/raw` 属于可被原子批次替换的输出生命周期；
- 是否复用反编译目录只看目录存在，不验证 artifact hash、JADX 版本和完成状态；
- JADX 1.5.6 与 A17 输入真实存在，但 live DB 没有证据支持旧 README 的大规模 Vendor 完成声明。

影响：部分/陈旧反编译可污染唯一 live 数据库；在修复前只能操作数据库副本。

### P1 — 构建不可复现（未修复）

live DB 的 574,399 个节点全部是 `source_revision = unknown`。build manifest 记录 build ID、时间和配置路径，但没有每个 repo 的 revision/dirty state，也没有 Vendor artifact/JADX identity。

影响：无法从数据库事实追溯到确切源码版本，不能可靠比较两次构建。

### P2 — 文档把存在性写成完成度（本分支已修复）

旧 README 宣称 Permission Phase 2a 和 Vendor 大规模融合完成，与数据库证据冲突。新文档只陈述已验证测试和带日期的 live 快照，未验证能力进入路线图而非完成表。

### P2 — 解析器覆盖仍有显式缺口（按设计保留）

Java/AIDL/Kotlin 有现有解析路径；C/C++、Rust、HIDL 只做语言探测。能力报告会显示 unsupported，strict 模式可失败，因此这不是静默丢失，但仍限制跨 native/HAL 分析。

### P3 — 本地工作树和发布操作债务（待收尾）

审查时存在一个旧的 `.worktrees/sync-wsl-generated-project` 链接工作树，且本地主分支领先远端。它们不改变代码语义，但会增加误提交/错误发布风险。应在本功能分支合入并确认不再需要后清理旧 worktree，再由用户决定远端发布。

## 已实施的架构修复

```text
Git project/ source
        │ payload allowlist + SHA-256 manifest
        ▼
adjacent staging install
        │ verify
        ▼
fresh rename / upgrade rollback
        │
        ▼
disposable WSL deployment
```

- `.gitignore` 已恢复为 UTF-8，并隔离快照/工作树/运行数据；
- fresh 不覆盖已有目标；
- upgrade 保留数据、venv 和本地配置，旧源码保留 rollback；
- setup 无交互提示，只有显式 `--rebuild` 扫描 AOSP；
- 安装失败不会把不完整 staging 提升为目标；
- drift verifier 明确列出 added/removed/modified。

## 下一步计划

1. 完成本分支的 WSL 临时目录 fresh/upgrade/verify 验收，并证明 live DB 未改变。
2. Permission：修复 XML capability 调度、错误报告、常量/多行解析和方法范围。
3. Vendor：把输入移出 `data/`，记录 SHA-256/JADX manifest，复用统一 lock/staging/atomic publish。
4. Provenance：采集 manifest 中每个 repo revision/dirty state，并写入节点/边来源。
5. 之后再开展 Build Graph、fingerprint 增量、Runtime/Test Graph 和 Agent 接口。

实施细节见 [可信源码与安装设计](../designs/2026-07-21-trustworthy-source-and-installation-baseline-design.md) 和 [实施计划](../plans/2026-07-21-trustworthy-source-and-installation-baseline-plan.md)。
