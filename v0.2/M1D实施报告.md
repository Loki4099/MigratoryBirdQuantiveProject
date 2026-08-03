# M1D实施报告：只读API与双语前端骨架

## 1. 结论

M1D已完成。M1阶段由工程、数据库、发布血缘、只读API和双语应用五部分组成的地基现已闭合，可以进入Catalog/Data纵向功能开发。

## 2. 只读API

- 新建独立`style_rotation.api`模块，不复用v0.1排行榜查询逻辑；
- 首批GET接口：health、capabilities、artifact列表/详情和lineage；
- 统一响应包含API/系统版本、只读标记和质量状态；
- artifact支持状态/类型筛选、limit/offset分页；
- published、draft、retired、superseded、tainted、invalidated映射为明确质量状态；
- 查询使用只读数据库事务；
- 列表、详情和lineage提供ETag与条件请求；
- 404、422等错误使用稳定`code/message`结构；
- OpenAPI固定输出到`v0.2/openapi.v2.json`并由测试检查同步；
- `style-rotation api`只允许127.0.0.1、localhost或::1，无账户阶段拒绝对外网卡监听。

## 3. React应用

- React 19、TypeScript 5.9、Vite 8、React Router、TanStack Query和i18next；
- 依赖使用精确版本并生成`pnpm-lock.yaml`；
- OpenAPI自动生成`schema.generated.ts`，生成文件不手改；
- zh-CN/en从第一版可用，语言同时进入URL和本地偏好；
- 建立Dashboard、Research、Products和System导航；
- 当前真实页面为Dashboard、Artifacts、Artifact Detail和API；
- 未开发领域只显示里程碑边界，不制造模拟研究结果；
- 统一Loading、Empty、Error和Quality Badge基础组件；
- tainted和invalidated具有文本与颜色双重提示；
- FastAPI提供Vite生产构建，并支持SPA深链接；
- 使用雏鸟/鸟蛋CSS标识作为临时个人元素，不将其当成M9正式Logo。

## 4. 版本选择说明

实现时核对了官方发布信息。Vite 8为稳定版本；TypeScript 7虽已发布，但OpenAPI生成器和typescript-eslint尚不兼容，因此锁定其共同支持的最新TypeScript 5.9.3。没有通过忽略peer warning掩盖不兼容。TanStack Table和图表库尚无真实消费者，延后到相应业务页面再引入。

## 5. 验证

- Python Ruff、Mypy和全量测试；
- OpenAPI committed/generated一致性；
- npm peer dependency检查无问题；
- ESLint、TypeScript检查、Vitest和Vite生产构建通过；
- 真实PostgreSQL→FastAPI→React静态托管集成测试通过；
- 本地浏览器检查桌面与390×844移动布局，无横向溢出；
- 中英文切换保留URL，4个真实catalog artifact正常展示；
- 浏览器控制台无warning/error。

## 6. 下一步

M2实现资产、标识、listing、universe、数据字段、source snapshot、canonical dataset、质量问题、calendar和data bundle。现有预留页面将在各自真实API可用后逐步替换，不一次性建设空壳页面。
