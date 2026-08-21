# M8 Recovery Drill Checkpoint

状态：完成（revision 73）
范围：数据库恢复身份、Object Store 强根逐对象回读、rollback 行为与 Release Gate

## 已落地契约

- `ops.v022_restore_drill_snapshot` 固化一次数据库与 Object Store 联合恢复演练。
  数据库部分只能引用 `ops.backup_record.status = restore_tested` 且带实际
  `restore_tested_at` 的备份；备份 schema revision、Git commit 与 dump SHA-256
  进入不可变 Evidence fingerprint。
- Object Store 盘点覆盖所有已发布、已物化且 retention class 为
  `product | evidence | export | legal_hold` 的 Payload Manifest。每个 Manifest/Object
  对必须记录期望/恢复后 SHA-256、字节数、通过状态与精确 blocker。
- 空强根不是成功：`no_materialized_strong_root_objects` 会生成可审计但
  `ready_for_gate = false` 的 Evidence。
- `ops.v022_rollback_drill_snapshot` 绑定一次已经发布的
  `maintenance_read_only` Release Transition，并固化：
  - `(execution_version_id, decision_session_id)` 重复 Product Decision 数；
  - rollback 时点后新增 Product Decision 数；
  - v0.21 已发布内容读取探针；
  - v0.22 mutation 拒绝探针；
  - exact pinned replay 探针。
- PostgreSQL trigger 会重新计算 Product Decision 计数，并验证 Restore Object
  确实来自对应强根 Manifest、源对象状态为 published/verified。
- Rollback 探针输入不再允许 operator 手工填写通过/失败。系统会验证历史 Artifact 与 pinned
  idempotency response 均早于 rollback transition，实际读取 Artifact identity、调用 mutation
  admission，并通过带“禁止执行 operation”回调的幂等层验证 exact replay 只返回冻结响应。
- Restore/rollback Snapshot 与成员均 append-only；Release Gate 只接受精确
  `v022_restore_drill_evidence` / `v022_rollback_drill_evidence` 类型且 ready 的 Artifact。
- Gate 使用 Restore Evidence 时重新盘点当前强根：演练后新增、发布或遗漏的强根
  会使旧证据立即失效，不能靠历史 `ready=true` 绕过。

## 失败关闭语义

- DB dump 没有经过真实 restore test；
- DB restore 不在本次 drill 时间窗口；
- 当前没有任何物化强根对象；
- 恢复对象缺失、SHA-256 不同、字节数不同；
- 源对象不是 published + verified；
- rollback 不是维护只读 Transition；
- rollback 后仍发布新 Product Decision；
- 任一访问、拒绝或 replay 探针失败；
- Release Gate 当下的强根集合不再等于演练集合。

## 验证覆盖

- 纯函数测试覆盖完整通过、哈希损坏、空盘点、意外对象。
- PostgreSQL 集成测试覆盖空强根失败关闭、真实本地 Payload Object 独立恢复回读、
  typed Gate 接受/拒绝、rollback canonical count 与 append-only 保护。
- 既有 Release Control 测试已改为确认任意通用 Artifact 不能冒充正式恢复证据。

## 后续 M8

API mutation guard、可信 actor、operator-only backup/restore、Object Store 回读、Restore Evidence
和 rollback probe CLI 均已落地。下一切片补齐最终 release runbook 与一键预检编排。
