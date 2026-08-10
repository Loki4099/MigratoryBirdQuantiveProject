# 外置 Cell Payload 维护运行手册

状态：仅允许人工维护，默认只执行 dry-run。本流程不得接入 Worker idle hook、定时任务、部署脚本、数据库迁移或应用启动流程，也不授权任何生产清理。

## 安全边界

- API、实验 Worker 与维护进程的 `STYLE_ROTATION_CELL_RESULT_DIRECTORY` 必须指向同一持久化共享目录。
- Product、当前 Suite、queued/running Work Item、任意 `experiment.cell_result` 引用全部保留。引用查询失败、引用文件缺失或路径异常时 fail-closed。
- 只识别根目录内严格命名的 `<64位小写十六进制哈希>.parquet`；不跟随符号链接，不处理其他文件。
- 默认宽限期为 7 天。只允许把超过宽限期且完全无引用的 payload 移入 quarantine。
- 发布方先创建 `.<hash>.<token>.pending`，数据库提交或回滚后再移除。发布与维护共享 `.cell-payload.lock`，维护在锁内重查数据库引用后才允许移动文件。
- 新鲜 marker 或 owner Work Item 仍活跃时，payload 与 marker 都保留。超过宽限期的崩溃残留 marker：若 DB 已引用，只隔离 marker；若无 DB/Product/当前/活动引用，可与 orphan payload 一同隔离；无法识别 owner 时 fail-closed。
- quarantine 不是永久删除。流程不包含 unlink/purge；receipt 和 rollback token 必须保留。

## 生成完整计划

在与应用完全相同的数据库配置和共享挂载下运行：

```python
import json
from pathlib import Path

from style_rotation.config.settings import get_settings
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.storage.maintenance import StorageMaintenanceService

settings = get_settings()
service = StorageMaintenanceService(create_postgres_engine(settings.database_url))
plan = service.dry_run_cell_payloads()
plan_path = Path("artifacts/maintenance-plans/cell-payload-plan.json")
service.write_cell_payload_plan(plan, plan_path)
print(json.dumps(plan.summary(), ensure_ascii=False, indent=2))
```

必须保存 `write_cell_payload_plan()` 生成的完整、hash-bound JSON。`summary()` 只能人工阅读，不能用于重建或执行计划。`blocked_reasons` 非空时立即停止。

## 人工审核

- 核对 `root_directory` 与所有进程的共享目录一致。
- 核对 Product、当前实验、活动工作项及普通 DB 引用均为 `keep`。
- payload 的隔离原因只能是 `unreferenced_grace_expired`。
- marker 的隔离原因只能是 `stale_marker_reference_committed` 或 `stale_marker_unreferenced`。
- 核对数量、字节数、路径、mtime、文件 SHA-256，确认计划文件未被编辑。
- 任何一项无法确认，都止于 dry-run并重新调查。

## 显式隔离与回滚

隔离只能通过完整计划和精确 token 执行：

```python
loaded = service.load_cell_payload_plan(plan_path)
receipt = service.execute_cell_payload_quarantine(
    loaded,
    confirmation_token=loaded.confirmation_token,
)
receipt_path = Path(receipt.quarantine_directory) / "operator-receipt.json"
service.write_cell_payload_receipt(receipt, receipt_path)
```

执行时会在共享锁内重新枚举 DB 引用与 pending marker，并重验路径、mtime、大小和 SHA-256。任何引用或文件状态发生变化，整次操作中止并回滚已移动文件。文件只移动到 `.maintenance-quarantine/<完整plan_id>/`。

回滚必须加载完整 receipt 并使用精确 token：

```python
loaded_receipt = service.load_cell_payload_receipt(receipt_path)
service.rollback_cell_payload_quarantine(
    loaded_receipt,
    confirmation_token=loaded_receipt.rollback_token,
)
```

回滚拒绝覆盖任何已存在的 live 文件，并再次核对 receipt 与文件 SHA-256。`rollback.json` 是审计证据，应与 plan、receipt 一并保留。

## 禁止事项

- 禁止永久清空 quarantine，禁止手工删除 payload、pending marker 或锁文件。
- 禁止仅因为实验不是 Product 就删除仍被 DB 行引用的 payload。
- 禁止仅凭文件年龄或磁盘压力跳过引用查询。
- 禁止编辑 token、复用旧计划、缩短宽限期后直接生产执行。
- 禁止把本流程用于 Workspace cache、lineage archive 或原始市场数据。

出现目录锁超时、引用查询失败、计划/receipt hash 不一致、引用文件缺失或部分移动时，立即停止，保留 plan、receipt、quarantine 和日志，优先恢复可读性后重新生成 dry-run。
