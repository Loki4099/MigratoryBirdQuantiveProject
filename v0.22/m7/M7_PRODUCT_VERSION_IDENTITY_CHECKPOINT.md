# M7 Product 三类版本身份检查点

日期：2026-08-11
状态：`passed`（M7 子检查点；M7 Gate 仍开放）

## 身份边界

本检查点按冻结实施基线区分一个稳定概念与三类独立版本：

- `Product Definition`：稳定的产品 key、名称和概念说明；
- `Product Execution Version`：冻结实际 Configuration、晋级来源 Evidence 与运行政策；
  任何会改变信号、权重或执行路径的变化必须发布新版本，并在后续建立新 Enrollment；
- `Qualification Version`：冻结其所属 Execution、评价 Evidence、门禁与附加资格证据；
  benchmark、Evaluation Target 或评价指标变化可以独立升版，不暗中改写 Execution；
- `Monitoring Policy Version`：冻结告警、健康度和监控计算政策；阈值变化独立升版，
  不重启 Execution/OOS。

`Product Definition` 不是“三类版本”之一。此前 Comparison 检查点结尾误写的
Definition / Deployment / Evaluation 已修正为上述最终口径。

## 发布与数据库约束

`ProductIdentityService` 为四类对象发布不可变 Artifact、语义 fingerprint 与完整依赖：

- Execution 的 promotion Evidence 必须已发布，并严格绑定同一 Research Configuration；
- Qualification 必须与 Execution 属于同一 Product，且评价 Evidence 的 Configuration
  必须等于该 Execution 的 Configuration；
- Qualification 附加证据以明确、有序的 Artifact dependencies 冻结；
- Monitoring 只依赖稳定 Product Definition，因此可独立演进；
- 同一 Product、同一类型、同一 version number 的不同语义会 fail closed，不能覆盖。

四张业务表均为 append-only，关键跨表约束在服务层与 PostgreSQL trigger 双重执行。

## 验证

- Python unit：393 passed；
- PostgreSQL database foundation + Graph Draft integration：9 passed；
- 验证 Definition、Execution、Qualification、Monitoring v1/v2 发布与幂等重放；
- 验证 Monitoring 升版不改变 Execution identity；
- 验证同版本不同 Monitoring 语义被拒绝；
- 验证空库升级、全量降级及再次升级；
- Ruff 与 strict mypy 通过。

下一检查点实现 Enrollment、OOS 冻结锚点与不可变 scheduled Product Decision 身份。
