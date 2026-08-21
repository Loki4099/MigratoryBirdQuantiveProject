# M8 Trusted Actor Checkpoint

状态：完成

## 身份来源

v0.22 首发仍遵守冻结的单用户、本地 loopback 部署边界，不引入虚假的多用户登录系统。
生产 `create_app()` 现在从服务端配置创建 `TrustedLocalActorContext`：

- `STYLE_ROTATION_API_ACTOR_KEY`：认证后的本地主体；
- `STYLE_ROTATION_API_OPERATOR_ENABLED`：是否同时授予 operator role；
- authentication source 固定为 `trusted_local_server_configuration`。

客户端 Header、Query 和请求体均不能改变该主体。测试或嵌入式调用只有显式注入依赖时才能
替换/关闭 Actor Context，这不属于生产启动路径。

## 兼容迁移

现有 v0.21/v0.22 请求 schema 暂时保留 `researcher_id`、`researcher_key`、`actor_key`，
但它们已经降级为 legacy assertion：

- 与服务端认证主体相同：业务服务只接收服务端主体；
- 不同：HTTP 403 `actor_claim_mismatch`，不进入 mutation admission、幂等或数据库业务层；
- 缺少所需 role：HTTP 403 `actor_role_denied`。

该方式保持当前前端/历史客户端兼容，同时消除了“请求体自报即成为数据库 actor”的授权漏洞。

## 覆盖入口

- v0.21 Draft save、Suite submit、Experiment promotion；
- v0.22 Graph Draft create/clone/event/change preview/confirm/compile；
- Product lifecycle、Alert status、Product review。

Signal 历史导出与 Suite cancel 当前请求不携带 actor claim；它们仍受上一切片的 Release State
scope 控制。后台 Worker 继续使用独立的受限 service principal，不复用研究者主体。

## 前端会话接口

新增 `GET /api/v2/session`，返回服务端 actor key、roles 与 authentication source。前端应以该
接口填充兼容字段，不再硬编码或允许用户编辑 actor。OpenAPI contract 已同步更新。

## 验证边界

- 纯函数测试覆盖 server actor、researcher/operator roles、claim mismatch 与 role denied；
- API 测试覆盖 session contract、伪造 Graph actor 返回 403 且 mutation admission 未执行；
- PostgreSQL 测试覆盖伪造 v0.21 Draft actor 后数据库 Draft 数仍为 0，再验证合法主体正常创建。

## 后续 M8

CLI 的数据库 upgrade/reset、backup create/restore-test、幂等响应修复和 Artifact invalidate 已要求服务端配置
授予 `operator` role；研究者身份会在接触数据库或文件写入前 fail closed。

`recovery publish-restore-evidence` 现在接收恢复后的 Object Store 根目录，并以数据库强 root inventory 中的
canonical `payload-object://` URI 逐对象读取真实字节、重算 SHA-256 与 byte size。缺失、hash 不同、大小不同
均发布为不可通过 Gate 的 blocker，路径逃逸和非 canonical URI 直接拒绝；客户端不能手填“观测 hash”冒充回读。

`recovery publish-rollback-evidence` 已将 rollback probe publication 接入 operator-only CLI。
探针不接受人工布尔结果，而是读取 rollback 前已发布的 v0.21 Artifact、验证当前 release admission
精确拒绝 v0.22 research mutation，并以 rollback 前冻结的 command name、idempotency key 和 request
fingerprint 实际调用幂等层；只允许返回既有响应，若触发业务 operation 则立即失败。

下一切片进入最终 release runbook 与一键预检编排。
