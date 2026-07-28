# Android Context Intelligence Canonical Project

本目录是 Git 仓库中唯一的可执行项目源码。它会由根目录的
`setup.sh` 安装到 WSL；已安装目录不是源码来源，也不应反向同步。

完整安装、环境要求和风险说明见 [仓库 README](../README.md)。

## 受管内容

```text
collectors/  config/  configs/  graph/  queries/
scripts/     storage/ tests/    workspace/
```

安装器还会在部署根目录生成 `.android-context-installation.json`，记录
来源 commit 和每个受管文件的 SHA-256。以下是运行状态，不属于 payload：

```text
.venv/  data/  caches  backups  vendor inputs  decompiler output
```

upgrade 保留 `.venv/`、`data/`、`config/source_roots.toml` 和
`configs/local.yaml`。

## 部署后的命令

```bash
cd /home/ts/android-context-intelligence

bash scripts/rebuild_all.sh --discover-only
bash scripts/rebuild_all.sh --plan-only
bash scripts/rebuild_all.sh
bash scripts/rebuild_all.sh --keep-failed-db
bash scripts/rebuild_all.sh --strict
```

原子重建先写入 `data/staging/<build-id>`，通过外键、服务链和报告验证后
再发布 `data/android_context.db`。中断恢复和并发排斥由
`workspace.build_publish` 与 `data/.rebuild.lock` 管理。

## 开发验证

从 Git 仓库根目录运行：

```bash
python -m pytest -q project/tests
python -m compileall -q project
bash -n project/scripts/rebuild_all.sh
```

当前源码已将 Permission Semantics Graph 接入原子重建和发布前验证。
Vendor 原子导入、跨仓库符号冲突治理和完整 source revision provenance
仍属于后续工作，不能因实验性入口存在就视为已完成。

## Permission Semantics Graph v0.1

执行计划能力名为 `permission_semantics`，产物报告为
`data/raw/permission/permission-semantics-report.json`。正式语义边为：

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

```bash
bash scripts/rebuild_all.sh --strict-capability permission_semantics
sqlite3 -header -column data/android_context.db \
  < queries/permission_semantics_summary.sql
python -m workspace.permission_validation \
  --db data/android_context.db \
  --report data/raw/permission/permission-semantics-report.json \
  --fingerprint
```

验证器在原子发布前检查报告字段、八类边端点、重复 active 边和 SQLite 外键。

升级安装会保留本地 `config/source_roots.toml`。现有部署必须确认
`frameworks/base` 的 `include` 包含 `data`，才能覆盖 platform privapp
策略；默认配置与本地覆盖的自动迁移属于下一阶段治理范围。
