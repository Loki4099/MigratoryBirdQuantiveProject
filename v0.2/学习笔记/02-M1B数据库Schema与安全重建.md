# M1B应用端说明：数据库Schema与安全重建

## Schema是什么

PostgreSQL schema可以理解为同一个数据库里的“命名空间”。例如`factor.factor_value`和`signal.signal_value`分别属于Factor和Signal领域。它不是另一台数据库，也不是另一个服务。

使用九个schema的优点是职责直观、同名对象不会冲突、权限和迁移容易按领域检查；缺点是SQL必须更明确，跨schema外键也比全部放在`public`稍复杂。

v0.2选择schema分区，是为了让数据库结构与代码和页面的领域边界一致，而不是为了追求技术复杂度。

## 为什么不迁移v0.1数据库

常见做法有两种：在旧表上连续升级，或建立全新基线。

连续升级适合已经发布、必须保留客户数据的系统，但需要大量兼容表和数据转换。全新基线会失去旧数据库记录，却能让结构与新设计完全一致。

v0.1没有正式发布，而且我们已经明确不要求跨版本复现，因此v0.2建立干净基线更合理。Git仍保留旧代码和设计，不等于数据库必须同时保存旧结构。

## 为什么测试数据库单独使用55432端口

迁移测试会反复删除schema。如果和日常开发数据库共用，很容易误删研究数据。`postgres-test`使用独立容器、独立volume、独立数据库名和独立端口。

最初计划使用5433，但该端口已经属于另一个项目。系统没有停止那个项目，而是改用55432，体现了“只修改当前项目范围”的原则。

## 安全重建做了哪些保护

`style-rotation db reset`只有同时满足以下条件才执行：

- PostgreSQL psycopg连接；
- localhost；
- environment为local或test；
- 数据库名是`style_rotation`或以`style_rotation_`开头；
- 不允许postgres/template数据库；
- `--confirm-database`与真实数据库名完全一致。

任何一项不满足都会在删除前失败。

## 基础约束有什么价值

- UUID不是可读业务含义，避免ticker或名称变化破坏引用；
- `timestamptz`保存带时区事件，避免本地时间歧义；
- text加CHECK让状态可验证，同时比数据库ENUM容易升级；
- `ON DELETE RESTRICT`阻止删除仍被下游引用的artifact；
- 唯一约束阻止同一身份或同一运行尝试重复写入；
- hash格式约束阻止明显损坏的发布身份进入数据库。

这些约束不能替代业务代码，但能在应用出现错误时提供最后一道保护。

## 如何检查它是否正常

```powershell
style-rotation db status --json
```

正常结果应显示：

- revision为`20260802_01_v02_foundation`；
- 九个schema全部存在；
- `missing_schemas`为空；
- `alembic check`报告没有新的升级操作；
- 集成测试能够从空库升级、降级到base、再升级。

你只需要记住：**生产逻辑依靠版本和血缘解释结果，数据库约束负责阻止结构上不可能成立的数据。**
