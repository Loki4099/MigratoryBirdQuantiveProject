# 项目资源清理记录（2026-08-13）

## 目的与边界

本次维护用于降低 OneDrive 同步、文件索引、杀毒扫描和开发工具文件监视的压力。
清理范围仅包含可由锁文件或工具明确重建的本地依赖、缓存和构建状态。

本次没有修改、移动或删除：

- Git 仓库、源码、测试、数据库迁移和配置；
- `v0.1`、`v0.2`、`v0.21`、`v0.22` 中的计划与设计资料；
- 用户创建的 DOCX、PDF 或其他原始资料；
- `artifacts`、数据库 dump、外置研究结果和 `.codex_work`；
- 项目外的 `%USERPROFILE%\.codex`、Codex 缓存和任务历史。

## Git 安全检查点

- 分支：`v0.2-rebuild`
- 清理时 HEAD：`8588b377b5d0ab6f93232b300b6dd930b3954acc`
- 相对 `origin/v0.2-rebuild`：ahead 65
- 清理前后均保留的既有工作树状态：
  - `src/style_rotation/v022/representative_pipeline_runtime.py` 已修改；
  - `v0.1` 下两份学习手册 DOCX/PDF 未跟踪；
  - 无暂存改动。

资源清理没有产生 Git 跟踪文件的删除或功能代码改动。

## 清理结果

| 指标 | 清理前 | 清理后 | 变化 |
|---|---:|---:|---:|
| 物理文件 | 59,507 | 5,281 | -54,226（约 -91%） |
| 目录 | 7,996 | 729 | -7,267 |
| 占用 | 3,990,549,791 bytes（3.716 GiB） | 2,580,008,582 bytes（2.403 GiB） | -1,410,541,209 bytes（1.313 GiB） |

已清理：

- `.venv`；
- `frontend/node_modules`；
- `.mypy_cache`、`.pytest_cache`、`.ruff_cache`、`.coverage`；
- 项目源码树中的 34 个 `__pycache__`；
- `frontend/*.tsbuildinfo`；
- `src/style_rotation_engine.egg-info`。

`frontend/node_modules` 包含指向用户级 pnpm store 的硬链接。清理只删除了工作区链接树，
没有删除或修改用户级 pnpm store。

## 清理后保留资源

| 目录 | 文件数 | 大小 |
|---|---:|---:|
| `artifacts` | 147 | 2,424.69 MiB |
| `.codex_work` | 49 | 8.70 MiB |
| `.git` | 4,271 | 15.10 MiB |
| `src` | 253 | 3.80 MiB |
| `tests` | 163 | 1.04 MiB |
| `migrations` | 85 | 1.03 MiB |
| `v0.1`–`v0.22` | 229 | 4.61 MiB |

以下 PostgreSQL custom-format dump 原样保留：

- `artifacts/v0.2-full-parameter-space.dump`；
- `artifacts/v0.2-long-history-canonical.dump`；
- `artifacts/v0.2-release.dump`；
- `artifacts/v0.2-main-release.dump`；
- `artifacts/v021-test-recovery.dump`。

这些不确定资源应在 v0.22 核心开发完成后，结合 `ops.backup_record`、SHA-256 和独立
restore-test 再决定迁移或删除。

## 环境重建

Python：

```powershell
py -3.12 -m venv C:\DevEnvs\signal-analysis-py312
C:\DevEnvs\signal-analysis-py312\Scripts\python.exe -m pip install -r requirements.lock
C:\DevEnvs\signal-analysis-py312\Scripts\python.exe -m pip install -e ".[dev]"
```

前端：

```powershell
cd frontend
pnpm install --frozen-lockfile
```

清理后的主机检查显示 pnpm 可用，但系统当前没有可由 `py` 找到的已安装 Python。
恢复后端开发前需先安装或明确配置 Python 3.12。建议把虚拟环境放在 OneDrive 之外，
不要重新创建项目内 `.venv`。Codex 桌面环境另有随应用提供的 Python 运行时，可用于短期
工具调用，但它不应替代项目自己的、可重复构建的开发环境。

本次清理归档后，已用 Codex 提供的 Python 3.12.13 在
`C:\DevEnvs\signal-analysis-py312` 创建外置虚拟环境，并按 `requirements.lock` 安装完成。
pytest、SQLAlchemy、Pydantic、FastAPI、Pandas、PyArrow、Alembic、mypy 和 Ruff 的导入检查
均通过。前端 `node_modules` 尚未重建；待仓库迁出 OneDrive 或确有前端工作需要时再恢复。

## 后续治理决定

1. v0.22 开发期间不再处理用途不确定的 dump、Parquet、实验结果和计划文档。
2. 新的 Python 虚拟环境放在 `C:\DevEnvs` 或其他非 OneDrive 目录。
3. v0.22 核心开发完成后，再审计 `artifacts` 的数据库引用与恢复能力。
4. 条件允许时，将活跃 Git 仓库整体迁移到 `C:\Projects`；OneDrive 仅保留文档或冷归档。
5. 资源治理提交与功能开发提交保持分离。
