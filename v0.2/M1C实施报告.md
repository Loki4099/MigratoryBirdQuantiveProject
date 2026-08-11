# M1C实施报告：发布与确定性核心

## 1. 结论

M1C已完成。系统现在可以把M0研究目录作为不可变artifact发布，稳定地区分“同一语义”“同一内容”和“同一依赖快照”，并由数据库强制保护发布冻结、依赖关系和状态审计。

## 2. 已交付

- `canonical-json-v2`：NFC文本、UTC时间、规范Decimal、小写UUID、精确float64字节表示、确定集合顺序和类型边界；
- 数值环境快照与hash：Python、平台、字节序及核心数值包版本；
- 第二条Alembic revision：lineage manifest、invalidation、`tainted`状态和数据库触发器；
- artifact发布服务：原子创建草稿、绑定已发布依赖、计算semantic fingerprint/content hash、切换published并保存展开manifest；
- 幂等与并发收敛：natural identity advisory lock确保相同发布请求只产生一个正式artifact；
- 生命周期保护：发布后身份和内容冻结，状态变化必须带事件ID和原因；
- 依赖保护：只允许草稿修改依赖，只能引用已发布上游，并递归拒绝依赖环；
- 失效传播：上游invalidated后，所有仍为published的下游递归标记为tainted；
- CLI：`bootstrap catalogs`、`artifact list/show/invalidate`和`lineage show`；
- M0四类目录的真实端到端bootstrap及重复复用验证。

## 3. 验证结果

- Ruff：通过；
- Mypy（M1C相关模块）：通过；
- 单元测试：67 passed；
- PostgreSQL集成测试：7 passed；
- Alembic：单一head为`20260802_02_v02_lineage`，`alembic check`无漂移；
- 并发验收：8次等价请求只创建1个artifact，其余7次复用；
- 重复bootstrap：factor、signal、model、strategy四个artifact ID、fingerprint、content hash和manifest hash全部保持一致；
- strategy catalog lineage可展开到四个目录artifact和四条直接/传递依赖边。

## 4. 审计问题的落实

- canonical serialization从模糊的JSON兼容表示升级为显式版本化、类型安全的v2格式；
- 严格hash边界增加数值环境身份，避免不同运行环境被误认为同一复现条件；
- 不可变和状态历史从应用约定提升为数据库触发器约束；
- 增加并发真实数据库测试，证明幂等不是只在串行单元测试中成立；
- invalidated不会删除历史，且下游tainted可被API/UI明确排除或警示。

## 5. 边界与下一步

M1C只建立通用发布骨架和研究目录bootstrap，尚未创建各业务层完整表，也没有对外HTTP接口。M1D将消费现有artifact查询服务建立只读`/api/v2`和双语前端骨架；M2以后各业务发布必须复用本阶段服务和数据库生命周期约束。
