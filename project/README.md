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

当前源码包含 Permission 和 Vendor 的基础采集器，但其存在不代表 live
数据库覆盖已完成。Permission 语义、Vendor 原子导入和 source revision
provenance 仍是下一阶段工作。
