# AMENDMENT-001：恢复冻结规范索引完整性

> 状态：accepted
>
> 修复日期：2026-08-12
>
> 影响范围：文档治理，不改变任何 v0.22 Graph、Catalog、编译或运行语义

`freeze-manifest.v0.22.0.json` 将 `README.md` 登记为冻结的 `normative_document_index`：

- 期望字节数：`2464`
- 期望 SHA-256：`c1f4463fb3b423009be55ca0ddea96567a460cd7c43f34a24e6b3be6b112c19a`

M0—M8 实现期间，若干历史提交直接在该冻结索引内更新了里程碑状态，导致当前 HEAD 的文件变为
2958 bytes、SHA-256 `a7b0942ee13585a8da6a419f64fcbb4e674398f964776e34a86da18f9df5ae33`，
但没有建立新 Contract Version 或更新冻结证明，因此 Freeze Manifest 的完整性检查失效。

本修订执行两件事：

1. 把 `v0.22/README.md` 恢复为 Freeze Manifest 冻结的精确内容；
2. 将后续工程进度、发布边界和产品信息架构 ADR 放入未冻结的
   [`README.md`](README.md) 修订索引。

这不是把工程状态回退到 M0，也不会撤销 M0—M8 的代码或证据；它只是让冻结规范索引重新满足
自身声明的内容寻址契约。当前工程状态与 Gate 边界以同目录的冻结后修订索引和 M8 Runbook 为准。
