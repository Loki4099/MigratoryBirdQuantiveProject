# AMENDMENT-005：双频启动修复、研究轮次重置与结果保留

> 状态：accepted / frozen
>
> 决策日期：2026-08-19
>
> 实现状态：pending
>
> 前置修订：AMENDMENT-002、AMENDMENT-003、AMENDMENT-004

本修订冻结 AMENDMENT-004 完成后的短期修复边界。目标是恢复“双频编译 →
实验运行 → 排行榜 → 结果详情 → Product”的完整用户主链，并把“重置当前研究”
改为具有明确保留和清理语义的 Research Round 操作。

本修订不改写已经发布的 Product、Result Evidence、Catalog、Dataset 或历史回测身份。
实现不得用清库、伪造结果、回退默认 4 ETF 或绕过不可变身份来掩盖问题。

## 1. 已确认的故障

### 1.1 双频训练型实验启动返回 500

周频和月频图使用相同的有序因子集合时，应共享同一个 Feature Schema 语义身份。
当前实现一方面以 `feature_schema_fingerprint` 发布并复用全局 Artifact，另一方面又把
`v022_feature_schema_version` 绑定为单个 Compiled Graph 的私有行。第二张频率图因此
重复插入相同 Artifact，触发唯一约束；仅增加 `ON CONFLICT` 也会使第二张图在运行时
无法按 Graph ID 找到 Schema。

### 1.2 Reset 的前端承诺与后端行为不一致

当前 Reset 会重建带默认 4 ETF、默认聚合器、默认策略和无防御的 Draft，而不是真正
清空研究。它还会 release 已完成 Graph Run 的 Consumer，使已完成 Suite 投影为
`completed / 0 / 0 / complete=false`；同时又无法精确覆盖双频 Launch Batch 的 Clone
Draft。旧 Suite、Evidence 和排行榜仍出现在普通实验界面。

### 1.3 结果数据存在，但用户入口和 Product 投影不完整

已发布 Result Evidence 含完整回测指标和路径；实验详情页也已经能够展示核心指标、
策略/基准净值、超额净值和回撤。问题是 Suite 结果入口不明显，部分被 Reset 损坏的
Suite 无法进入详情；Product 详情只提供“打开冻结回测”的链接，没有直接展示来源
回测。Product 尚未到首个前瞻决策日时没有 OOS 曲线属于正确状态，不得伪造。

## 2. Feature Schema 与双频启动冻结语义

1. Feature Schema 是由有序特征、类型、known-at 和 missing policy 决定的全局不可变
   身份；频率或 Graph ID 不得被人为加入 fingerprint 以制造重复 Schema。
2. 新增 Compiled Aggregation Instance 到 Feature Schema Version 的精确绑定。每个
   编译实例必须绑定一个已发布 Schema；多个周/月实例可以绑定同一个 Schema。
3. Compiler 必须先按 Artifact/fingerprint 精确复用或发布 Schema，再原子写入实例绑定；
   不得为每张图无条件插入 Schema 行。
4. Aggregation、训练、Product 和证据运行时必须从精确实例绑定解析 Schema ID；不得再
   依赖 `compiled_research_graph_id` 猜测。
5. 已部分创建的 Launch Batch 必须可用原 idempotency key 继续：已成功的源图不重建，
   缺失的频率图继续编译，两个子 Suite 均完成提交后才锁定源研究。
6. Launch Batch 必须持久记录阶段、失败代码和安全摘要。数据库完整性异常不得裸露为
   500；前端必须显示“实验启动失败”及所处频率/阶段，不得误报为“研究配置保存失败”。

## 3. Research Round 与 Reset 冻结语义

### 3.1 研究轮次

- 一个用户研究根在任意时刻恰好有一个 active Research Round；
- Draft Revision、Compile、Launch Batch、周/月子 Suite 和排名发布均精确绑定 Round；
- 历史现有数据迁移为可解释的初始 Round，不按时间或“latest”猜测归属；
- 排行榜与实验历史默认只读取 active Round，不把已关闭 Round 混入当前研究。

### 3.2 Reset 单事务行为

确认 Reset 后必须：

1. 锁定当前 active Round，并阻止新的编译/启动命令进入该 Round；
2. 枚举该 Round 的完整 Launch Batch，包括周/月 Clone Draft；
3. 只取消尚未终态的 Run/Work，绝不 release 或改写已完成 Run 的 Consumer；
4. 关闭旧 Round，并立即从普通实验历史和排行榜隐藏该 Round；
5. 建立新的 active Round 和真正未配置的 Draft；
6. 清除前端 compile、launch、suite 和查询缓存，导航回资产选择页；
7. 返回取消、保留、待清理对象数量，供二次确认结果和运维审计使用。

Reset 不得同步执行大规模物理删除；用户确认后旧普通实验立即不可见，物理清理由受控
GC 完成。

### 3.3 新 Round 的空白状态

- 仅保留界面默认频率 `weekly`；
- 资产、因子、聚合器/模型、Target、训练预设、策略和防御全部未选择；
- Draft 可以处于 `asset_context=unconfigured`，此时不发布或伪造 Dataset Binding；
- 选择合法资产并解析数据绑定后，才能继续加工、编译和启动；
- `us_style_rotation_4_etf_sample_v1` 不再作为创建或 Reset 默认值。

已有 4 ETF 身份、Dataset 和被历史 Product/Result 引用的血缘保持可读；本修订不因移除
默认行为而删除历史身份。

## 4. 保留根与普通实验清理

### 4.1 永久保留根

任何已经升级为 Product 的配置，无论 Product 后续 active、paused 或 retired，必须永久
保留其 exact：

- Product Definition、Execution、Qualification、Enrollment、Monitoring 和 Model State；
- Configuration Snapshot、Result Evidence、Portfolio Cell Result 与完整回测指标/路径；
- 复现所需的 Graph、Plan、Spec、Manifest、Payload、模型、数据和血缘；
- Catalog、Evaluation Cohort、Dataset/Universe/Gate 及披露身份。

共享对象只要仍被 active Round、Product、Catalog、Dataset 或其他强根引用，就不得清理。

### 4.2 未升级普通实验

关闭 Round 中未升级为 Product 的普通实验不作长期保存：

1. Reset 提交后立即从用户界面隐藏并标记 `gc_pending`；
2. 等待非终态 Worker 确认取消或 fencing 失效；
3. 以强根可达性计算清理集合，而不是按文件名、创建时间或目录猜测；
4. 先删除/evict 未被引用的对象存储 Payload、模型和中间产物，再按外键顺序清理普通
   Suite、Run、Result、Evidence、配置和 Draft/Batch 身份；
5. 多实验共享的加工缓存只在最后一个强根消失后清理；
6. 只保留极小的 GC tombstone，包括 Round、Reset、状态、数量、完成时间和失败摘要，
   不保留普通实验的完整结果内容。

GC 必须幂等、可重试、分批执行并具有 dry-run/计划输出。Reset API 不得等待大对象清理
完成，也不得在部分删除后把 Round 重新显示为 active。

## 5. 已完成 Suite 的可读性修复

- Suite 的历史完成状态、计数和 Result 可读性必须来自已发布 Plan、Work 终态和 Result
  身份，不得以 `consumer.released_at IS NULL` 作为历史存在性的唯一判据；
- Consumer release 只表达当前 Run 不再消费共享 Work，不能抹除已经完成的事实；
- 现有被投影为 `completed / 0 / 0` 的 Suite 必须通过修正查询恢复可读，不回填或伪造
  Result；
- 关闭 Round 后普通 Suite 默认不可见，但 Product 的精确来源 Evidence 仍可由 Product
  页面访问。

## 6. 实验与 Product 展示

### 6.1 实验

- 排行榜一行对应一个 exact Portfolio Cell/Result Evidence；
- 排行榜整行或醒目按钮进入完整结果详情；
- Suite 的“本次运行详情”必须为每个已发布结果提供“查看完整回测”入口；
- 完整详情展示 CAGR、SPY CAGR、年化超额、Sharpe、最大回撤，以及策略/SPY 净值、
  超额净值、回撤三张图；训练型结果继续展示 OOF/Target/成员诊断；
- 周频和月频继续按精确 Evaluation Cohort 分榜，不混排。

### 6.2 Product

Product 详情必须明确分成两个独立区域：

1. **晋升时冻结的研究回测**：直接投影 Product 强引用的 Source Result Evidence，展示
   同一套核心指标、三张图、配置和数据警告，不重新计算；
2. **激活后的前瞻 OOS**：只使用 Product 激活后发布的决策与监控。首个合法决策日前
   明确显示等待状态，不使用历史回测填充 OOS 图表。

实验详情和 Product 来源回测应复用同一个只读 Backtest Evidence 组件和 API 语义，避免
两套指标解释漂移。

## 7. 实施顺序

1. Feature Schema 全局身份、实例绑定迁移与历史 backfill；
2. Compiler、Aggregation/训练/Product Runtime 按绑定读取；
3. Launch Batch 持久失败状态、结构化错误和部分批次恢复；
4. Research Round、Round 绑定和 active-scope 查询；
5. Reset 精确取消、空白新 Round、移除默认 4 ETF；
6. Suite 历史完成投影修复和现有 0/0 可读性恢复；
7. 强根可达性 GC、dry-run、异步 Worker 和 tombstone；
8. 实验入口、共享回测面板和 Product 双区域展示；
9. 完成聚焦验证后，再做一条真实双频主链验收。

每一步单独提交，不和模型扩展、数据抓取、v0.21 清理或性能重构混合。

## 8. 必要测试与验收

### 8.1 启动与运行

- 相同因子 Schema 的 weekly/monthly 图均编译成功并共享 exact Schema Version；
- 两个 Compiled Aggregation Instance 各有精确绑定，篡改或缺失绑定 fail closed；
- 双频 Launch Batch 生成两张图和两个 Suite；同 idempotency key 可恢复当前部分批次；
- weekly/monthly 各完成至少一个训练型 Cell，证明运行时不是只通过编译；
- deterministic-only 双频路径保持通过；启动错误返回稳定代码而非通用 500。

### 8.2 Reset、Round 与 GC

- 一个 active Round、Round 关闭和新 Round 创建原子完成；重复 Reset 幂等；
- 周/月未完成任务均取消，已完成 Suite/Result 不被改写；
- Reset 后普通实验立即从当前历史和排行榜消失；
- 新 Round 只有 weekly 界面默认，其他选择全部为空且不存在默认 4 ETF 数据绑定；
- Product 强根及其 Result/Evidence/血缘/对象全部保留；
- 未升级普通实验 dry-run 给出确定清单，执行后不可达对象被清理、共享对象不被误删；
- GC 中断可重试，且只产生一条最终 tombstone；
- 现有 `completed / 0 / 0` Suite 通过查询修复恢复正确状态和结果详情。

### 8.3 前端与结果

- Suite 结果和排行榜均能进入 exact Evidence 详情；
- 实验详情显示五项核心指标和三张发布路径图；
- Product 直接显示相同来源回测，并与 OOS 区域严格分离；
- OOS 尚未开始时显示等待，不显示伪造指标；
- 完成真实浏览器烟测：选择资产与因子 → 编译 → 双频启动 → 两频完成 → 排行榜 →
  详情 → 晋升 Product → Reset → 当前实验为空 → Product 来源回测仍可读。

## 9. 明确不做

- 不以频率或 Graph ID 污染 Feature Schema fingerprint；
- 不通过清库、删除当前部分 Batch 或重新提交新配置绕过启动故障；
- 不在 Reset 请求内同步递归删除大对象；
- 不删除 Product 强根、共享 Dataset/Catalog/加工缓存或历史 4 ETF 引用身份；
- 不把历史回测冒充 Product 前瞻 OOS；
- 不在本修复中扩展新模型、抓取新数据、清理 v0.21 或顺带进行大规模性能优化。
