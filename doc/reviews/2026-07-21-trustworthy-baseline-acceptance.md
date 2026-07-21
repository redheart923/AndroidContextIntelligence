# Trustworthy Source and Installation Baseline 验收记录

日期：2026-07-21

分支：`codex/trustworthy-source-baseline`

验收起始提交：`7f64b99fc61f9d40d339837afcf2f78cdcf6cf04`

临时目标：`/home/ts/aci-trustworthy-baseline-test`

## 结论

Trustworthy Source and Installation Baseline 的完成门槛已通过。Windows Git 仓库中的 `project/` 是唯一规范源码；`setup.sh` 能把它确定性安装到任意 WSL 目标，能检测受管文件漂移，并能在升级时保留运行数据和本地配置。

本次验收只操作临时目标，没有执行 `--rebuild`，也没有运行 Vendor 导入。现有 `/home/ts/android-context-intelligence` 的数据库与构建清单在验收前后保持完全一致。

## 1. 真实 WSL 项目保护基线

验收前记录：

```text
database: /home/ts/android-context-intelligence/data/android_context.db
size: 1775685632
mtime: 1784548161
sha256: 81c17817a1a913404d4cacf98a6fd12f3315e42e1ca87089734b20a21116d8e5
graph build: 20260720T062141Z-38707-556db1f7

build manifest: /home/ts/android-context-intelligence/data/workspace/build-manifest.json
sha256: 5c064e68738603dc5c4a87e8792692e1979c8bd6d858388b4d2ead80a32ca794
```

验收后重新计算得到完全相同的大小、mtime、SHA-256 和 `GRAPH_BUILD` 节点。由此证明临时安装验收没有改写真实项目数据库或构建清单。

## 2. Fresh 安装

执行：

```bash
PROJECT_ROOT=/home/ts/aci-trustworthy-baseline-test \
AOSP_ROOT=/home/ts/does-not-exist \
ANDROID_CONTEXT_SOURCE_COMMIT=7f64b99fc61f9d40d339837afcf2f78cdcf6cf04 \
bash /mnt/d/AndroidContextIntelligence/.worktrees/trustworthy-source-baseline/setup.sh --fresh
```

结果：

```text
fresh installation: PASS (/home/ts/aci-trustworthy-baseline-test)
Android Context Intelligence setup: PASS
```

无效的 `AOSP_ROOT` 不影响不带 `--rebuild` 的安装，证明安装和 AOSP 扫描已经解耦。安装清单记录：

```text
source_commit: 7f64b99fc61f9d40d339837afcf2f78cdcf6cf04
managed files: 55
```

## 3. 安装完整性与项目测试

执行 `setup.sh --verify-only`：

```text
payload verification: PASS (55 managed files)
Android Context Intelligence setup: PASS
```

在临时安装树中使用现有项目虚拟环境运行：

```bash
cd /home/ts/aci-trustworthy-baseline-test
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q
```

结果：

```text
54 passed in 3.44s
```

以下检查也通过且无输出：

```bash
/home/ts/android-context-intelligence/.venv/bin/python \
  -m compileall -q /home/ts/aci-trustworthy-baseline-test

find /home/ts/aci-trustworthy-baseline-test -type f -name '*.sh' \
  -print0 | xargs -0 -r -n1 bash -n
```

## 4. 漂移检测与 Upgrade 保留策略

在临时目标中创建以下保留项：

```text
data/acceptance-sentinel
.venv/acceptance-sentinel
configs/local.yaml
config/source_roots.toml
```

随后用 `/etc/hostname` 覆盖受管文件 `workspace/cli.py`。升级前执行 `--verify-only` 得到预期失败：

```text
payload verification: FAIL
modified: workspace/cli.py
exit code: 1
```

执行 `setup.sh --upgrade` 后：

```text
upgrade installation: PASS (/home/ts/aci-trustworthy-baseline-test)
rollback source retained at:
  /home/ts/.install-rollback-aci-trustworthy-baseline-test-323e061a1a904df3a11715029da5e154
```

升级后的证据：

- `data/acceptance-sentinel` 存在；
- `.venv/acceptance-sentinel` 存在；
- `configs/local.yaml` 存在；
- `config/source_roots.toml` 存在；
- `config/source_roots.toml` 升级前后 SHA-256 均为 `5018cc89500a472d7ba6e3bb8572bba0dd0cf7d468dffa73aed8d85fb9e3e5af`；
- `configs/local.yaml` 升级前后 SHA-256 均为 `f0646e5daa25c0e25e731b3dd47cefd6c5f06c9e8d29d07d02a0e87c16cc4dc3`；
- 再次执行 `--verify-only` 得到 `payload verification: PASS (55 managed files)`；
- 回滚目录存在，可用于人工审计或恢复。

这证明升级会替换漂移的受管源码，同时保留明确声明的运行数据和本地配置。

## 5. Windows 仓库最终验证

功能实现完成后的完整验证命令为：

```bash
cd /mnt/d/AndroidContextIntelligence/.worktrees/trustworthy-source-baseline
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q
/home/ts/android-context-intelligence/.venv/bin/python -m pytest -q project/tests
/home/ts/android-context-intelligence/.venv/bin/python -m compileall -q project scripts
bash -n setup.sh
find installers project/scripts -type f -name '*.sh' \
  -print0 | xargs -0 -r -n1 bash -n
```

最终新鲜运行结果：

```text
root suite: 48 passed in 27.11s
canonical project suite from repository root: 54 passed in 9.38s
final upgraded temporary installation suite: 54 passed in 2.65s
Python compileall: PASS
Bash syntax checks: PASS
```

验收期间还修复了一个由此命令发现的工作目录依赖：canonical 集成测试及其 Python 模块子进程现在均从 `project/` 定位资源，不再假设调用者当前目录恰好是 `project/`。

Git 完整性检查从 Windows worktree 执行：

```powershell
git diff --check
git status --short
```

最终结果应与本记录对应的验收提交一起保存，不应把临时 WSL 目录加入 Git。

## 6. 未包含在本里程碑的工作

本次验收不代表以下图层已经达到生产级完整性：

- Permission XML 调度、常量解析和多行调用语义；
- Vendor 导入的共享锁、暂存数据库和原子发布；
- repo revision、dirty state、Vendor artifact 和 JADX 身份的完整 provenance；
- Kotlin、C/C++、Rust 和 HIDL 的语义解析器。

下一阶段应按上述顺序推进，并继续遵循“一项行为变更、一组失败测试、一个聚焦提交”的 Git 纪律。
