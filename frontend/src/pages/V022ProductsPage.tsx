import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { FrozenBacktestPanel } from "./ExperimentResultPage";

type ProductDisplay = {
  direct_inputs?: Array<{ name?: string; variant_key?: string }>;
  aggregation?: {
    name?: string;
    family_key?: string;
    trainable_ensemble?: {
      member_count?: number;
      target_groups?: Array<{
        target_key?: string;
        target_name?: string;
        members?: Array<{ training_preset_key?: string; training_preset_name?: string }>;
      }>;
    };
  };
  strategy?: { name?: string; variant_key?: string; parameter_preset?: { name?: string } };
  defense?: { name?: string; none?: boolean; variant_key?: string };
};

type ProductEnsembleState = {
  state_version_number?: number;
  member_count?: number;
  state_fingerprint?: string;
  activated_session?: string;
  state_document?: {
    failure_policy?: string;
    members?: Array<{
      ordinal?: number;
      target_key?: string;
      training_preset_key?: string;
      adapter_key?: string;
      state_fingerprint?: string;
    }>;
  };
};

const warningLabels: Record<string, { zh: string; en: string }> = {
  free_data_research_product: { zh: "使用免费数据源的研究型 Product", en: "Research Product using free data sources" },
  historical_membership_retrospective: { zh: "历史成分基于回溯整理，可能存在残余偏差", en: "Historical membership is reconstructed retrospectively" },
  retrospective_price_snapshot: { zh: "价格为冻结的回溯复权快照，并非供应商原生 PIT", en: "Prices are frozen retrospective adjusted snapshots, not provider-native PIT" },
  uniform_provider_exclusions_present: { zh: "存在对所有策略统一生效的数据排除", en: "Uniform provider exclusions apply to every strategy" },
  manual_gap_resolutions_present: { zh: "存在经人工复核并留痕的数据缺口修复", en: "Reviewed and recorded manual gap resolutions are present" },
  free_source_market_gaps: { zh: "免费行情仍存在已记录的数据缺口", en: "Recorded market-data gaps remain in free sources" },
  provider_uniformly_unavailable: { zh: "部分证券在免费供应商中统一不可获得", en: "Some securities are uniformly unavailable from free providers" },
  reviewed_execution_day_market_gap: { zh: "存在经复核的执行日行情缺口", en: "A reviewed execution-day market gap is recorded" },
  frozen_data_repair_uniform_exclusion: { zh: "本轮数据修复已将明确异常证券统一排除", en: "Confirmed anomalous securities were uniformly excluded by the frozen repair" },
  closure_review_weekly_adjusted_return_over_50_percent: { zh: "周频闭包中保留了待复核的单日复权收益超过 50% 记录", en: "Daily adjusted moves above 50% remain recorded for review in the weekly closure" },
  closure_review_monthly_adjusted_return_over_50_percent: { zh: "月频闭包中保留了待复核的单日复权收益超过 50% 记录", en: "Daily adjusted moves above 50% remain recorded for review in the monthly closure" },
  alternate_source_observations_present: { zh: "部分行情使用了经复核并冻结的备用免费数据源", en: "Some observations use reviewed and frozen alternate free sources" },
};

const localizedStatus = (value: string, chinese: boolean) => {
  const labels: Record<string, [string, string]> = {
    active: ["运行中", "Active"], observing: ["观察中", "Observing"],
    healthy: ["正常", "Healthy"], suspended: ["已暂停", "Suspended"],
    retired: ["已退役", "Retired"], invalidated: ["已失效", "Invalidated"],
    research_candidate: ["研究候选", "Research candidate"],
  };
  return labels[value]?.[chinese ? 0 : 1] ?? value;
};

const decisionState = (value: string, chinese: boolean) => {
  const labels: Record<string, [string, string]> = {
    inactive: ["当前不运行", "Inactive"],
    scheduled: ["等待计划日", "Scheduled"],
    waiting_for_input: ["等待合格数据", "Waiting for eligible data"],
    input_prepared: ["输入已冻结", "Input frozen"],
    runtime_published: ["运行产物已发布", "Runtime published"],
    schedule_complete: ["计划已完成", "Schedule complete"],
  };
  return labels[value]?.[chinese ? 0 : 1] ?? value;
};

const decisionStateDescription = (value: string, chinese: boolean) => {
  const labels: Record<string, [string, string]> = {
    inactive: ["该 Product 已暂停、退役或失效，不会继续发布新决策。", "This Product is suspended, retired, or invalidated and will not publish new decisions."],
    scheduled: ["尚未到达下一个冻结计划日；现在无需操作。", "The next frozen decision cutoff has not arrived; no action is required."],
    waiting_for_input: ["计划日已到，正在等待同一方法、覆盖完整且通过 Product Gate 的新数据发布。", "The cutoff has arrived and the Product is waiting for a same-methodology, fully covered Dataset Gate."],
    input_prepared: ["该计划日的数据、成员和 Gate 已冻结，Product Worker 将执行决策。", "Data, membership, and Gate are frozen for this session; the Product Worker will execute it."],
    runtime_published: ["加工、聚合、策略和合并产物已持久化，正等待不可变决策发布完成。", "Processing, aggregation, strategy, and merge outputs are persisted; immutable decision publication is pending."],
    schedule_complete: ["冻结调度中已无待处理计划日。", "No pending session remains in the frozen schedule."],
  };
  return labels[value]?.[chinese ? 0 : 1] ?? value;
};

export function V022ProductsPage() {
  const { enrollmentId } = useParams();
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const catalog = useQuery({ queryKey: ["v022", "products"], queryFn: api.v022Products, refetchInterval: 5000 });
  const detail = useQuery({
    queryKey: ["v022", "products", enrollmentId],
    queryFn: () => api.v022Product(enrollmentId!),
    enabled: Boolean(enrollmentId),
    refetchInterval: 5000,
  });

  if (catalog.isLoading || (enrollmentId && detail.isLoading)) return <LoadingState />;
  const error = catalog.error ?? detail.error;
  if (error) return <ErrorState error={error} retry={() => void (enrollmentId ? detail.refetch() : catalog.refetch())} />;
  if (enrollmentId && detail.data) return <V022ProductDetail data={detail.data} chinese={chinese} />;
  if (!catalog.data) return <EmptyState />;

  return <div className="page products-page">
    <header className="page-heading"><div><p className="eyebrow">V0.22 PRODUCT / PROSPECTIVE OOS</p><h1>{chinese ? "研究候选 Product" : "Research candidate Products"}</h1><p>{chinese ? "每个 Product 只来自一条冻结实验结果，并从激活后开始独立记录样本外决策与监控。" : "Every Product originates from one frozen experiment result and records prospective OOS decisions and monitoring after activation."}</p></div><QualityBadge state={catalog.data.quality.state} /></header>
    <section className="scope-strip"><div><span>{chinese ? "总数" : "Total"}</span><strong>{catalog.data.items.length}</strong></div><div><span>{chinese ? "运行中" : "Active"}</span><strong>{catalog.data.items.filter((item) => item.lifecycle === "active").length}</strong></div><div><span>{chinese ? "定义" : "Identity"}</span><strong>Result Evidence → Product</strong></div><div><span>{chinese ? "模式" : "Mode"}</span><strong>{chinese ? "前瞻样本外" : "Prospective OOS"}</strong></div></section>
    {catalog.data.items.length === 0 ? <section className="workspace-default-card"><div><h2>{chinese ? "还没有 Product" : "No Product yet"}</h2><p>{chinese ? "请在实验排行榜或结果详情中升级一条通过质量门禁的配置。" : "Promote one quality-passed configuration from the experiment leaderboard or result detail."}</p></div><Link className="arrow-link" to="/experiments">{chinese ? "打开实验排行榜" : "Open experiment leaderboard"} →</Link></section> : <section className="product-card-grid">{catalog.data.items.map((item) => {
      const display = item.display as ProductDisplay;
      return <Link className="product-candidate-card" to={`/products/${item.product_enrollment_id}`} key={item.product_enrollment_id}>
        <div><span>v0.22 · {localizedStatus(item.lifecycle, chinese)}</span><QualityBadge state={item.health === "healthy" ? "ok" : "partial"}>{localizedStatus(item.health, chinese)}</QualityBadge></div>
        <h2>{item.name}</h2><p>{display.aggregation?.name ?? "—"} · {display.strategy?.name ?? "—"}</p>
        {item.product_eligibility === "eligible_with_warnings" && <p className="scope-note">{chinese ? "免费数据研究 Product · 带永久质量披露" : "Free-data research Product · permanent quality disclosure"}</p>}
        <dl><div><dt>{chinese ? "防御" : "Defense"}</dt><dd>{display.defense?.none ? (chinese ? "无防御" : "No defense") : display.defense?.name ?? "—"}</dd></div><div><dt>{chinese ? "频率" : "Frequency"}</dt><dd>{item.frequency === "weekly" ? (chinese ? "周频" : "Weekly") : (chinese ? "月频" : "Monthly")}</dd></div><div><dt>{chinese ? "下一决策日" : "Next decision"}</dt><dd>{item.next_pending_decision_session ?? "—"}</dd></div><div><dt>{chinese ? "决策流程" : "Decision pipeline"}</dt><dd>{decisionState(item.decision_pipeline_state, chinese)}</dd></div></dl>
      </Link>;
    })}</section>}
  </div>;
}

type Detail = Awaited<ReturnType<typeof api.v022Product>>;

function V022ProductDetail({ data, chinese }: { data: Detail; chinese: boolean }) {
  const sourceEvidenceId = data.source_result_evidence_snapshot_id;
  const sourceBacktest = useQuery({
    queryKey: ["v022", "experiment-result", sourceEvidenceId],
    queryFn: () => api.v022Experiment(sourceEvidenceId),
    staleTime: Infinity,
  });
  const sourceSeries = useQuery({
    queryKey: ["v022", "experiment-result", sourceEvidenceId, "series"],
    queryFn: () => api.v022ExperimentSeries(sourceEvidenceId),
    staleTime: Infinity,
  });
  const display = (data.display ?? {}) as ProductDisplay;
  const latest = data.latest_decision as { session_date?: string; decision_status?: string; decision_document?: unknown } | null;
  const lifecycle = data.lifecycle ?? "unknown";
  const health = data.health ?? "unknown";
  const monitoringSnapshots = data.monitoring_snapshots ?? [];
  const warningCodes = data.warning_codes ?? [];
  const dataDisclosure = data.data_disclosure ?? {};
  const ensembleState = data.active_ensemble_state as ProductEnsembleState | null;
  return <div className="page products-page">
    <Link className="arrow-link" to="/products">← {chinese ? "返回 Product 列表" : "Back to Products"}</Link>
    <header className="page-heading"><div><p className="eyebrow">V0.22 PRODUCT / {lifecycle.toUpperCase()}</p><h1>{data.name}</h1><p>{chinese ? "该 Product 冻结自一条具体实验结果；研究回测与激活后的样本外记录严格分离。" : "This Product is frozen from one exact experiment result. Research backtest and post-activation OOS records remain separate."}</p></div><QualityBadge state={health === "healthy" ? "ok" : "partial"}>{localizedStatus(health, chinese)}</QualityBadge></header>
    <section className="scope-strip"><div><span>{chinese ? "生命周期" : "Lifecycle"}</span><strong>{localizedStatus(lifecycle, chinese)}</strong></div><div><span>{chinese ? "状态" : "Health"}</span><strong>{localizedStatus(health, chinese)}</strong></div><div><span>{chinese ? "激活时间" : "Activated"}</span><strong>{data.activation_effective_at ? new Date(data.activation_effective_at).toLocaleString() : "—"}</strong></div><div><span>{chinese ? "监控快照" : "Monitoring snapshots"}</span><strong>{monitoringSnapshots.length}</strong></div></section>
    <section className={`workspace-default-card ${data.product_eligibility === "eligible_with_warnings" ? "warning-card" : ""}`}><div><p className="eyebrow">PRODUCT DATA DISCLOSURE</p><h2>{chinese ? "数据口径与已知限制" : "Data basis and known limitations"}</h2><p>{data.product_data_disclosure_id ? (chinese ? "该 Product 冻结自历史成分 PIT 与回溯价格快照；所有免费数据修复、统一排除和备用源均随版本保留。" : "This Product freezes historical-membership PIT and retrospective prices; free-source repairs, exclusions, and alternates remain versioned.") : (chinese ? "这是在 M107 披露契约之前创建的历史 Product；旧身份保持可读，但未回填新的披露。" : "This legacy Product predates the M107 disclosure contract and remains readable without synthetic backfill.")}</p></div><QualityBadge state={data.product_eligibility === "eligible" ? "ok" : "partial"}>{data.product_eligibility === "eligible" ? (chinese ? "符合资格" : "Eligible") : (chinese ? "带数据警告" : "Data warnings")}</QualityBadge>{warningCodes.length > 0 && <ul>{warningCodes.map((code) => <li key={code}><strong>{warningLabels[code]?.[chinese ? "zh" : "en"] ?? code}</strong><code>{code}</code></li>)}</ul>}</section>
    {Object.keys(dataDisclosure).length > 0 && <details className="catalog-section"><summary>{chinese ? "查看冻结数据身份与未来输入政策" : "View frozen data identities and future-input policy"}</summary><pre>{JSON.stringify(dataDisclosure, null, 2)}</pre></details>}
    {sourceBacktest.isLoading || sourceSeries.isLoading
      ? <section className="catalog-section"><LoadingState /></section>
      : sourceBacktest.data && sourceSeries.data
        ? <FrozenBacktestPanel detail={sourceBacktest.data} series={sourceSeries.data} chinese={chinese} />
        : <section className="catalog-section warning-card"><div className="section-heading"><div><p className="eyebrow">SOURCE RESULT / RETENTION ERROR</p><h2>{chinese ? "冻结研究回测暂时不可读" : "Frozen research backtest is temporarily unavailable"}</h2><p>{chinese ? "Product 身份仍然有效，但来源 Evidence 或曲线读取失败；系统不会用空值或重新计算的结果替代它。" : "The Product identity remains valid, but its source Evidence or series could not be read. The system will not replace it with empty or recomputed values."}</p></div><Link className="arrow-link" to={`/experiments/results/${sourceEvidenceId}`}>{chinese ? "检查来源证据" : "Inspect source evidence"} →</Link></div></section>}
    <section className="catalog-section"><div className="section-heading"><div><p className="eyebrow">SOURCE RESULT / FROZEN CONFIGURATION</p><h2>{chinese ? "来源实验与配置" : "Source experiment and configuration"}</h2></div><Link className="arrow-link" to={`/experiments/results/${sourceEvidenceId}`}>{chinese ? "打开完整证据" : "Open full evidence"} →</Link></div><div className="result-configuration-grid"><article><span>{chinese ? "进入聚合的因子 / 信号" : "Inputs entering aggregation"}</span><ul>{(display.direct_inputs ?? []).map((item) => <li key={item.variant_key}><strong>{item.name ?? item.variant_key}</strong><code>{item.variant_key}</code></li>)}</ul></article><article><span>{chinese ? "聚合器" : "Aggregator"}</span><strong>{display.aggregation?.name ?? "—"}</strong><code>{display.aggregation?.family_key}</code>{display.aggregation?.trainable_ensemble && <div className="ensemble-summary"><p>{chinese ? `${display.aggregation.trainable_ensemble.member_count ?? 0} 个内部成员` : `${display.aggregation.trainable_ensemble.member_count ?? 0} internal members`}</p>{display.aggregation.trainable_ensemble.target_groups?.map((group) => <small key={group.target_key}>{group.target_name ?? group.target_key}: {group.members?.map((member) => member.training_preset_name ?? member.training_preset_key).join(" · ")}</small>)}</div>}</article><article><span>{chinese ? "策略" : "Strategy"}</span><strong>{display.strategy?.name ?? "—"}</strong><p>{display.strategy?.parameter_preset?.name}</p></article><article><span>{chinese ? "防御" : "Defense"}</span><strong>{display.defense?.none ? (chinese ? "无防御" : "No defense") : display.defense?.name ?? "—"}</strong></article></div></section>
    {ensembleState && <section className="catalog-section"><div className="section-heading"><div><p className="eyebrow">ACTIVE MODEL STATE / COMPLETE ATOMIC SET</p><h2>{chinese ? "当前生效的模型状态" : "Active model state"}</h2><p>{chinese ? "Product 的每次前瞻决策只使用这组完整、已发布的冻结模型；重训练失败时继续保留上一组完整状态。" : "Every prospective decision uses this complete published frozen model set. A failed retrain retains the previous complete state."}</p></div><QualityBadge state="ok">v{ensembleState.state_version_number ?? 1}</QualityBadge></div><div className="scope-strip"><div><span>{chinese ? "成员数" : "Members"}</span><strong>{ensembleState.member_count ?? ensembleState.state_document?.members?.length ?? 0}</strong></div><div><span>{chinese ? "激活计划日" : "Activated session"}</span><strong>{ensembleState.activated_session ?? "—"}</strong></div><div><span>{chinese ? "失败政策" : "Failure policy"}</span><strong>{ensembleState.state_document?.failure_policy === "retain_previous_complete_state" ? (chinese ? "保留上一完整状态" : "Retain prior complete state") : ensembleState.state_document?.failure_policy ?? "—"}</strong></div><div><span>{chinese ? "状态指纹" : "State fingerprint"}</span><code>{ensembleState.state_fingerprint?.slice(0, 16) ?? "—"}…</code></div></div><div className="diagnostic-card-grid">{ensembleState.state_document?.members?.map((member) => <article key={`${member.ordinal}:${member.target_key}:${member.training_preset_key}`}><span>{chinese ? `成员 ${Number(member.ordinal ?? 0) + 1}` : `Member ${Number(member.ordinal ?? 0) + 1}`}</span><h3>{member.target_key ?? "—"}</h3><p>{member.training_preset_key ?? "—"}</p><code>{member.adapter_key ?? "—"}</code></article>)}</div></section>}
    <section className="catalog-section"><div className="section-heading"><div><p className="eyebrow">PROSPECTIVE OOS</p><h2>{chinese ? "样本外决策与监控" : "OOS decisions and monitoring"}</h2></div><QualityBadge state={data.decision_pipeline_state === "waiting_for_input" ? "warning" : "ok"}>{decisionState(data.decision_pipeline_state, chinese)}</QualityBadge></div><article className={`workspace-default-card ${data.decision_pipeline_state === "waiting_for_input" ? "warning-card" : ""}`}><div><h3>{decisionState(data.decision_pipeline_state, chinese)}</h3><p>{decisionStateDescription(data.decision_pipeline_state, chinese)}</p>{data.next_pending_decision_cutoff_at && <p className="scope-note">{chinese ? "下一截止时间" : "Next cutoff"}：{new Date(data.next_pending_decision_cutoff_at).toLocaleString()}</p>}{data.next_product_input_available_at && <p className="scope-note">{chinese ? "输入冻结时间" : "Input frozen at"}：{new Date(data.next_product_input_available_at).toLocaleString()}</p>}</div></article><div className="scope-strip"><div><span>{chinese ? "已发布决策" : "Published decisions"}</span><strong>{data.decision_count}</strong></div><div><span>{chinese ? "已完成" : "Completed"}</span><strong>{data.completed_decision_count}</strong></div><div><span>{chinese ? "缺失" : "Missing"}</span><strong>{data.missing_decision_count}</strong></div><div><span>{chinese ? "下一计划日" : "Next scheduled"}</span><strong>{data.next_pending_decision_session ?? "—"}</strong></div><div><span>{chinese ? "最新决策日" : "Latest decision"}</span><strong>{latest?.session_date ?? data.latest_decision_session ?? "—"}</strong></div><div><span>{chinese ? "频率" : "Frequency"}</span><strong>{data.frequency === "weekly" ? (chinese ? "周频" : "Weekly") : data.frequency === "monthly" ? (chinese ? "月频" : "Monthly") : "—"}</strong></div></div>{latest ? <details><summary>{chinese ? "查看最新不可变决策" : "View latest immutable decision"}</summary><pre>{JSON.stringify(latest.decision_document ?? latest, null, 2)}</pre></details> : <p className="scope-note">{data.first_eligible_decision_session ? (chinese ? `首个可执行决策日为 ${data.first_eligible_decision_session}；到期前 Product 保持观察状态。` : `The first eligible decision session is ${data.first_eligible_decision_session}; the Product remains under observation until then.`) : (chinese ? "尚未发布首个可执行决策日。" : "The first eligible decision session has not been published yet.")}</p>}</section>
  </div>;
}
