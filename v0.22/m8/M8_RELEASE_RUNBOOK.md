# M8 v0.22.0 Release 与回退 Runbook

状态：已冻结

本 Runbook 只适用于计划冻结的单用户、本地/私有部署边界。所有写操作必须由服务端配置授予
`operator` role；命令中的 Artifact ID 必须来自当前数据库的 published Evidence，不得使用示例值。

## 1. 通用原则

- 先运行 `release-preflight`，确认 `ready=true`，再执行同参数的 `release-transition`。
- preflight 只读；transition 会再次运行相同校验，校验与写入之间状态变化会由数据库状态机/触发器拒绝。
- `requested_by` 永远取可信本地主体，CLI 不允许自报 actor。
- 每次 transition 必须提供稳定 `reason-code` 与人类可读 `reason`。
- 任一 blocker、告警未处理、Evidence 过期或强根集合变化时停止，不得修改数据库绕过 Gate。

## 2. 备份与联合恢复演练

```powershell
style-rotation backup create --output D:\BirdBackups\v022\database.dump --git-commit <GIT_COMMIT> --docker-service postgres
style-rotation backup object-create --backup-record-id <BACKUP_RECORD_ID> --bundle-root D:\BirdBackups\v022\strong-objects
style-rotation backup restore-joint --backup-record-id <BACKUP_RECORD_ID> --docker-service postgres --bundle-root D:\BirdBackups\v022\strong-objects --restored-object-root D:\BirdRestoreDrill\v022\objects
```

Restore Evidence 必须覆盖当下所有 `product | evidence | export | legal_hold` 的已发布物化强根。任何对象缺失、
hash/大小不同，或 Evidence 发布后强根集合变化，default preflight 都会失败。

数据库 dump、对象复制与 restore 均使用文件流，不会把大型备份一次性载入内存。`restore-joint` 除核对 Alembic
revision 和全部项目 schema 外，还会在受限 `search_path = pg_catalog` 下执行 canonical fingerprint
探针，并在临时恢复数据库删除前，将其中的强根 Manifest 闭包与对象包逐项精确比对；PostgreSQL
扩展、函数 schema、对象 hash/大小或联合身份不完整时必须失败。对象包是确定性的目录结构，包含
`strong-root-bundle.json` 与 `sha256/` 内容寻址文件，可迁移到非 OneDrive 存储；恢复目标必须是独立目录，
不得覆盖生产 Object Store。`restore-joint` 成功后会自动发布 Restore Evidence。

创建数据库 dump 与对象包期间必须保持写入静止（通常先进入维护只读窗口）。若两次快照之间强根发生
变化，联合恢复的数据库闭包比对会 fail closed；不得通过修改 Manifest 或复制“最新”对象绕过。
单独的 `restore-test`、`object-restore` 与 `recovery publish-restore-evidence` 仍保留用于故障定位，不能替代
最终一次完整 `restore-joint`。

## 3. 正常晋级

### hidden → shadow

```powershell
style-rotation recovery release-preflight --target shadow --evidence shadow_plan_artifact_id=<SHADOW_PLAN_ID>
style-rotation recovery release-transition --target shadow --reason-code begin_shadow --reason "start controlled representative dual run" --evidence shadow_plan_artifact_id=<SHADOW_PLAN_ID>
```

### shadow → explicit_eligible

```powershell
style-rotation recovery release-preflight --target explicit_eligible --evidence parity_gate_artifact_id=<PARITY_GATE_ID>
style-rotation recovery release-transition --target explicit_eligible --reason-code open_explicit --reason "v0.22 parity gate passed" --evidence parity_gate_artifact_id=<PARITY_GATE_ID>
```

### explicit_eligible → default

以下五类 Evidence 缺一不可：`parity_gate_artifact_id`、`shadow_coverage_artifact_id`、
`operations_readiness_artifact_id`、`restore_drill_artifact_id`、`rollback_drill_artifact_id`。

```powershell
style-rotation recovery release-preflight --target default --evidence parity_gate_artifact_id=<PARITY_GATE_ID> --evidence shadow_coverage_artifact_id=<SHADOW_COVERAGE_ID> --evidence operations_readiness_artifact_id=<OPS_READINESS_ID> --evidence restore_drill_artifact_id=<RESTORE_DRILL_ID> --evidence rollback_drill_artifact_id=<ROLLBACK_DRILL_ID>
style-rotation recovery release-transition --target default --reason-code v022_default_cutover --reason "all v0.22.0 release gates passed" --evidence parity_gate_artifact_id=<PARITY_GATE_ID> --evidence shadow_coverage_artifact_id=<SHADOW_COVERAGE_ID> --evidence operations_readiness_artifact_id=<OPS_READINESS_ID> --evidence restore_drill_artifact_id=<RESTORE_DRILL_ID> --evidence rollback_drill_artifact_id=<ROLLBACK_DRILL_ID>
```

## 4. 事故回退

先写入不可为空的 JSON Incident Document，例如 incident ID、影响范围、首次发现时间和止损动作，然后：

```powershell
style-rotation recovery release-preflight --target maintenance_read_only --incident-file <INCIDENT_JSON>
style-rotation recovery release-transition --target maintenance_read_only --reason-code runtime_incident --reason "freeze new research mutations during incident response" --incident-file <INCIDENT_JSON>
```

进入维护只读后，新 Draft/Compile/Suite/Promotion 被数据库权威 release state 拒绝；历史读取/导出保留，
active v0.21 Product 继续 pinned runtime，受影响的 v0.22 Enrollment 不得偷偷切换 calculator。

## 5. Rollback drill 与恢复提交

Rollback probe 只能使用 rollback 前已经发布的 v0.21 Artifact 和 rollback 前已经冻结的幂等命令响应：

```powershell
style-rotation recovery publish-rollback-evidence --rollback-transition-artifact-id <ROLLBACK_TRANSITION_ID> --v021-artifact-id <V021_ARTIFACT_ID> --replay-command-name <COMMAND_NAME> --replay-idempotency-key <IDEMPOTENCY_KEY> --replay-request-file <EXACT_REQUEST_JSON> --completed-at <ISO_OFFSET_DATETIME>
```

离开 `maintenance_read_only` 额外要求：`incident_impact_analysis_artifact_id`、parity、最新 restore drill 和
rollback drill。若直接恢复到 default，还必须同时提供 shadow coverage 与 operations readiness。仍按“先
preflight，后 transition”执行。

## 6. 完成确认

- preflight 与 transition 输出的 current/from/to state 符合预期；
- `/api/v2/release-control` 与数据库最新 published Transition 一致；
- API mutation admission 与目标状态矩阵一致；
- 没有重复 Product Decision、未解释 Shadow 差异或未处理 critical alert；
- 保存命令输出、Incident、Evidence Artifact ID 与 Git commit，禁止事后改写 Snapshot/Transition。
