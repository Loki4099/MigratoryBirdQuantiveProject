import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import { ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

const percent = (value: string | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(Number(value));
const number = (value: string | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(Number(value));

export function ExperimentResultPage() {
  const { evidenceId = "" } = useParams();
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const detail = useQuery({
    queryKey: ["v022", "experiment-result", evidenceId],
    queryFn: () => api.v022Experiment(evidenceId),
    enabled: Boolean(evidenceId),
  });
  const series = useQuery({
    queryKey: ["v022", "experiment-result", evidenceId, "series"],
    queryFn: () => api.v022ExperimentSeries(evidenceId),
    enabled: Boolean(evidenceId),
  });
  const promote = useMutation({
    mutationFn: async () => {
      if (!detail.data) throw new Error("Result detail is not loaded");
      const display = detail.data.display as {
        aggregation?: { name?: string };
        strategy?: { name?: string; parameter_preset?: { name?: string } };
      };
      const name = [
        display.aggregation?.name,
        display.strategy?.name,
        display.strategy?.parameter_preset?.name,
      ].filter(Boolean).join(" · ") || `v0.22 ${evidenceId.slice(0, 8)}`;
      return api.promoteAndEnrollV022Product(evidenceId, {
        idempotencyKey: crypto.randomUUID(),
        productKey: `result_${evidenceId.replaceAll("-", "").slice(0, 24)}`,
        name,
        description: "Promoted from one exact frozen v0.22 Result Evidence.",
      });
    },
    onSuccess: () => void detail.refetch(),
  });

  if (detail.isLoading || series.isLoading) return <LoadingState />;
  if (detail.error) return <ErrorState error={detail.error} retry={() => void detail.refetch()} />;
  if (series.error) return <ErrorState error={series.error} retry={() => void series.refetch()} />;
  if (!detail.data || !series.data) return null;

  const data = detail.data;
  const display = data.display as {
    direct_inputs?: Array<{ name?: string; variant_key?: string; parameters?: Record<string, unknown> }>;
    aggregation?: {
      name?: string;
      family_key?: string;
      trainable_ensemble?: {
        member_count?: number;
        combination_policy?: string;
        target_groups?: Array<{
          target_key?: string;
          target_name?: string;
          members?: Array<{ training_preset_key?: string; training_preset_name?: string }>;
        }>;
      };
    };
    strategy?: { name?: string; variant_key?: string; parameter_preset?: { name?: string; parameters?: Record<string, unknown> } };
    defense?: { name?: string; none?: boolean; variant_key?: string };
  };
  const comparison = data.comparison_context;
  const diagnosticEnvelope = data.evidence.trainable_aggregation_diagnostic as {
    diagnostic_fingerprint?: string;
    diagnostic_document?: TrainableDiagnosticDocument;
  } | undefined;
  const trainableDiagnostic = diagnosticEnvelope?.diagnostic_document;
  const product = data.product as {
    is_candidate?: boolean;
    is_enrolled?: boolean;
    product_enrollment_id?: string | null;
  };
  const canPromote = comparison != null && data.outcome === "accepted"
    && data.quality_status === "passed" && !product.is_enrolled;
  const productEnrollmentId = promote.data?.product_enrollment_id ?? product.product_enrollment_id;

  return <div className="page experiment-result-page">
    <header className="page-heading"><div>
      <p className="eyebrow">V0.22 / IMMUTABLE PORTFOLIO CELL RESULT</p>
      <h1>{chinese ? "实验结果详情" : "Experiment result detail"}</h1>
      <p>{chinese ? "该页面只投影已发布的配置、指标、路径、质量和血缘，不会重新运行回测。" : "This page projects published configuration, metrics, paths, quality and lineage without rerunning the backtest."}</p>
    </div><Link className="arrow-link" to="/experiments">← {chinese ? "返回排行榜" : "Back to leaderboard"}</Link></header>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">FROZEN COMPARISON CONTEXT</p><h2>{comparison?.cohort_key ?? (chinese ? "旧动态区间结果" : "Legacy dynamic-range result")}</h2></div><QualityBadge state={data.quality_status === "passed" ? "ok" : "warning"} /></div>
      <div className="scope-strip experiment-scope-strip">
        <div><span>{chinese ? "频率" : "Frequency"}</span><strong>{comparison?.frequency ?? String(data.configuration.frequency ?? "—")}</strong></div>
        <div><span>{chinese ? "评价区间" : "Evaluation range"}</span><strong>{comparison ? `${comparison.evaluation_start} → ${comparison.evaluation_end}` : `${data.effective_start} → ${data.effective_end}`}</strong></div>
        <div><span>{chinese ? "基准" : "Benchmark"}</span><strong>{comparison?.benchmark_key?.toUpperCase() ?? "SPY"}</strong></div>
        <div><span>{chinese ? "单边成本" : "Cost per side"}</span><strong>{comparison?.cost_bps_per_side ?? "—"} bps</strong></div>
      </div>
      {!comparison && <p className="scope-note warning">{chinese ? "该结果早于统一 Evaluation Cohort，只保留历史查看，不进入主排行榜或 Product 升级。" : "This result predates the strict Evaluation Cohort. It remains readable but cannot enter the leaderboard or Product promotion."}</p>}
    </section>

    <FrozenBacktestPanel detail={data} series={series.data} chinese={chinese} />

    {trainableDiagnostic && <section className="catalog-section trainable-diagnostic-section">
      <div className="section-heading"><div><p className="eyebrow">STRICT OOF / MODEL GROUP DIAGNOSTICS</p><h2>{chinese ? "模型组样本外诊断" : "Model-group out-of-fold diagnostics"}</h2></div></div>
      <p className="scope-note">{chinese ? "以下指标只使用每个成员严格样本外预测，并分别与该成员自己的 Target 比较；不会使用最终 Portfolio 收益来调节模型权重。" : "These metrics use strict out-of-fold member predictions and compare each member only with its own Target. Portfolio outcomes never tune ensemble weights."}</p>
      <div className="diagnostic-summary-strip">
        <div><span>{chinese ? "内部成员" : "Members"}</span><strong>{trainableDiagnostic.member_count}</strong></div>
        <div><span>Target</span><strong>{trainableDiagnostic.target_group_count}</strong></div>
        <div><span>{chinese ? "严格 OOF 行" : "Strict OOF rows"}</span><strong>{trainableDiagnostic.panel_row_count.toLocaleString()}</strong></div>
        <div><span>{chinese ? "组合回测消融" : "Portfolio ablation"}</span><strong>{chinese ? "需独立冻结运行" : "Separate frozen run required"}</strong></div>
      </div>
      <div className="diagnostic-card-grid">
        {trainableDiagnostic.member_diagnostics.map((member) => <article key={`${member.target_key}:${member.training_preset_key}`}>
          <span>{member.target_key}</span><h3>{member.training_preset_key}</h3>
          <dl><dt>Mean Rank IC</dt><dd>{number(member.predictive.mean_rank_ic)}</dd><dt>IC IR</dt><dd>{number(member.predictive.ic_ir)}</dd><dt>{chinese ? "正 IC 比例" : "Positive IC ratio"}</dt><dd>{percent(member.predictive.positive_ic_ratio)}</dd><dt>{chinese ? "折数" : "Folds"}</dt><dd>{member.fold_count}</dd></dl>
        </article>)}
      </div>
      <div className="diagnostic-card-grid">
        {trainableDiagnostic.final_ensemble_by_target.map((item) => <article key={item.target_key}>
          <span>{chinese ? "最终 Ensemble 对该 Target" : "Final Ensemble against Target"}</span><h3>{item.target_key}</h3>
          <dl><dt>Mean Rank IC</dt><dd>{number(item.predictive.mean_rank_ic)}</dd><dt>Median Rank IC</dt><dd>{number(item.predictive.median_rank_ic)}</dd><dt>IC IR</dt><dd>{number(item.predictive.ic_ir)}</dd><dt>{chinese ? "正 IC 比例" : "Positive IC ratio"}</dt><dd>{percent(item.predictive.positive_ic_ratio)}</dd></dl>
        </article>)}
      </div>
      <div className="diagnostic-card-grid">
        {trainableDiagnostic.target_group_diagnostics.map((group) => <article key={group.target_key}>
          <span>{chinese ? "Target 组" : "Target group"}</span><h3>{group.target_key}</h3>
          <p>Mean Rank IC <strong>{number(group.predictive.mean_rank_ic)}</strong> · IC IR <strong>{number(group.predictive.ic_ir)}</strong></p>
          {group.within_target_member_ablations.length > 0 && <details><summary>{chinese ? "同 Target 留一成员诊断" : "Within-Target leave-one-member diagnostics"}</summary><ul>{group.within_target_member_ablations.map((item) => <li key={item.omitted_training_preset_key}>{chinese ? "移除" : "Omit"} <code>{item.omitted_training_preset_key}</code>: Δ IC {number(item.full_minus_reduced_mean_rank_ic)}</li>)}</ul></details>}
        </article>)}
      </div>
      {trainableDiagnostic.pairwise_prediction_correlations.length > 0 && <details><summary>{chinese ? "成员预测相关性" : "Member prediction correlations"}</summary><div className="diagnostic-correlation-list">{trainableDiagnostic.pairwise_prediction_correlations.map((item) => <p key={`${item.left_target_key}:${item.left_training_preset_key}:${item.right_target_key}:${item.right_training_preset_key}`}><code>{item.left_target_key} / {item.left_training_preset_key}</code><span>↔</span><code>{item.right_target_key} / {item.right_training_preset_key}</code><strong>{number(item.mean_cross_sectional_rank_correlation)}</strong></p>)}</div></details>}
    </section>}

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">FROZEN CONFIGURATION</p><h2>{chinese ? "本实验使用的配置" : "Configuration used by this experiment"}</h2></div></div>
      <div className="result-configuration-grid">
        <article><span>{chinese ? "进入聚合的因子 / 信号" : "Inputs entering aggregation"}</span><ul>{(display.direct_inputs ?? []).map((item) => <li key={item.variant_key}><strong>{item.name ?? item.variant_key}</strong><code>{item.variant_key}</code></li>)}</ul></article>
        <article><span>{chinese ? "聚合器 / 模型" : "Aggregator / model"}</span><strong>{display.aggregation?.name ?? "—"}</strong><code>{display.aggregation?.family_key}</code>{display.aggregation?.trainable_ensemble && <div className="ensemble-summary"><p>{chinese ? `${display.aggregation.trainable_ensemble.member_count ?? 0} 个内部模型成员；Target 间等权、同 Target 超参数间等权` : `${display.aggregation.trainable_ensemble.member_count ?? 0} internal model members; equal across Targets and within-Target presets`}</p>{display.aggregation.trainable_ensemble.target_groups?.map((group) => <div key={group.target_key}><strong>{group.target_name ?? group.target_key}</strong><small>{group.members?.map((member) => member.training_preset_name ?? member.training_preset_key).join(" · ")}</small></div>)}</div>}</article>
        <article><span>{chinese ? "策略" : "Strategy"}</span><strong>{display.strategy?.name ?? "—"}</strong><p>{display.strategy?.parameter_preset?.name}</p><code>{display.strategy?.variant_key}</code></article>
        <article><span>{chinese ? "防御" : "Defense"}</span><strong>{display.defense?.none ? (chinese ? "无防御" : "No defense") : display.defense?.name ?? "—"}</strong><code>{display.defense?.variant_key}</code></article>
      </div>
    </section>

    <section className="catalog-section experiment-promotion-card">
      <div><p className="eyebrow">PRODUCT CANDIDATE / OOS OBSERVATION</p><h2>{productEnrollmentId ? (chinese ? "已升级并开始样本外观察" : "Promoted and observing OOS") : (chinese ? "升级当前配置" : "Promote this configuration")}</h2><p>{chinese ? "只升级当前这一条 Result Evidence；后复权研究数据警告会保留，正式决策映射回实际市场价格。" : "Only this Result Evidence is promoted. The adjusted-price research warning remains and formal decisions map back to market prices."}</p></div>
      {promote.error && <ErrorState error={promote.error} retry={() => promote.mutate()} />}
      {productEnrollmentId
        ? <Link className="arrow-link" to={`/products/${productEnrollmentId}`}>{chinese ? "打开 Product" : "Open Product"} →</Link>
        : <button type="button" disabled={!canPromote || promote.isPending} onClick={() => {
          if (window.confirm(chinese ? "确认只将当前实验配置升级为 Product，并立即开始样本外观察？" : "Promote only this experiment configuration and start OOS observation?")) promote.mutate();
        }}>{promote.isPending ? (chinese ? "正在升级…" : "Promoting…") : (chinese ? "升级 Product" : "Promote Product")}</button>}
      {!canPromote && !productEnrollmentId && <p className="scope-note">{chinese ? "仅统一 Cohort 中 accepted / passed 的结果可以升级。" : "Only accepted, passed results in a strict Cohort may be promoted."}</p>}
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">QUALITY / EVIDENCE / LINEAGE</p><h2>{chinese ? "数据质量与证据" : "Data quality and evidence"}</h2></div></div>
      <div className="experiment-detail-actions"><Link className="arrow-link" to={`/artifacts/${data.result_artifact_id}`}>Result Artifact →</Link><Link className="arrow-link" to={`/artifacts/${data.evidence_artifact_id}`}>Evidence Artifact →</Link></div>
      <details><summary>{chinese ? "查看完整指标与冻结证据" : "View all metrics and frozen evidence"}</summary><pre>{JSON.stringify({ metrics: data.metrics, quality: data.evidence_quality, evidence: data.evidence }, null, 2)}</pre></details>
    </section>
  </div>;
}

type PathPoint = Awaited<ReturnType<typeof api.v022ExperimentSeries>>["points"][number];
type PathField = "strategy_nav" | "benchmark_nav" | "excess_nav" | "drawdown";
type BacktestDetail = Awaited<ReturnType<typeof api.v022Experiment>>;
type BacktestSeries = Awaited<ReturnType<typeof api.v022ExperimentSeries>>;

export function FrozenBacktestPanel({
  detail,
  series,
  chinese,
}: {
  detail: BacktestDetail;
  series: BacktestSeries;
  chinese: boolean;
}) {
  return <section className="catalog-section frozen-backtest-panel">
    <div className="section-heading"><div><p className="eyebrow">CORE PERFORMANCE / IMMUTABLE EVIDENCE</p><h2>{chinese ? "冻结研究回测" : "Frozen research backtest"}</h2><p>{chinese ? "指标和路径直接来自来源 Result Evidence，不会在 Product 页面重新计算。" : "Metrics and paths are projected directly from the source Result Evidence and are never recomputed on the Product page."}</p></div></div>
    <div className="product-performance-strip">
      <div><span>CAGR</span><strong>{percent(detail.core_metrics.cagr)}</strong></div>
      <div><span>{chinese ? "SPY 年化" : "SPY CAGR"}</span><strong>{percent(detail.core_metrics.benchmark_cagr)}</strong></div>
      <div><span>{chinese ? "年化超额" : "Annualized excess"}</span><strong>{percent(detail.core_metrics.cagr_spread)}</strong></div>
      <div><span>Sharpe</span><strong>{number(detail.core_metrics.sharpe_ratio)}</strong></div>
      <div><span>{chinese ? "最大回撤" : "Maximum drawdown"}</span><strong>{percent(detail.core_metrics.maximum_drawdown)}</strong></div>
    </div>
    <div className="experiment-chart-grid">
      <ResultPathChart title={chinese ? "策略净值 vs SPY" : "Strategy NAV vs SPY"} points={series.points} fields={["strategy_nav", "benchmark_nav"]} />
      <ResultPathChart title={chinese ? "超额净值" : "Excess NAV"} points={series.points} fields={["excess_nav"]} />
      <ResultPathChart title={chinese ? "回撤" : "Drawdown"} points={series.points} fields={["drawdown"]} />
    </div>
  </section>;
}

type PredictiveDiagnostic = {
  mean_rank_ic: string | null;
  median_rank_ic: string | null;
  positive_ic_ratio: string | null;
  ic_ir: string | null;
};

type TrainableDiagnosticDocument = {
  member_count: number;
  target_group_count: number;
  panel_row_count: number;
  member_diagnostics: Array<{
    target_key: string;
    training_preset_key: string;
    fold_count: number;
    predictive: PredictiveDiagnostic;
  }>;
  target_group_diagnostics: Array<{
    target_key: string;
    predictive: PredictiveDiagnostic;
    within_target_member_ablations: Array<{
      omitted_training_preset_key: string;
      full_minus_reduced_mean_rank_ic: string | null;
    }>;
  }>;
  final_ensemble_by_target: Array<{
    target_key: string;
    predictive: PredictiveDiagnostic;
  }>;
  pairwise_prediction_correlations: Array<{
    left_target_key: string;
    left_training_preset_key: string;
    right_target_key: string;
    right_training_preset_key: string;
    mean_cross_sectional_rank_correlation: string | null;
  }>;
};

function ResultPathChart({ title, points, fields }: { title: string; points: PathPoint[]; fields: PathField[] }) {
  if (points.length < 2) return <article className="experiment-chart"><h3>{title}</h3><p>—</p></article>;
  const values = points.flatMap((point) => fields.map((field) => Number(point[field])));
  const minimum = Math.min(...values); const maximum = Math.max(...values); const span = maximum - minimum || 1;
  const paths = fields.map((field) => points.map((point, index) => `${(index / (points.length - 1) * 100).toFixed(2)},${(42 - (Number(point[field]) - minimum) / span * 38).toFixed(2)}`).join(" "));
  return <article className="experiment-chart"><h3>{title}</h3><svg viewBox="0 0 100 46" role="img" aria-label={title}>{paths.map((path, index) => <polyline key={fields[index]} className={`series-${index}`} points={path} />)}</svg><footer><span>{points[0].session_date}</span><span>{points.at(-1)?.session_date}</span></footer></article>;
}
