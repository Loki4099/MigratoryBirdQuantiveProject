import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { DecisionExplorer } from "../components/DecisionExplorer";
import { ResearchKey, researchLabel } from "../components/ResearchText";

const ratio = (value: number | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value);
const decimal = (value: number | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value);
const percentageMetrics = new Set([
  "cumulative_return", "cagr", "annualized_volatility", "maximum_drawdown",
  "positive_daily_return_ratio", "best_daily_return", "worst_daily_return",
  "positive_monthly_return_ratio", "best_monthly_return", "worst_monthly_return",
  "cumulative_relative_return", "annualized_relative_wealth_growth", "cagr_spread",
  "tracking_error", "annualized_alpha",
]);
const metricValue = (metric: { metric_key: string; value: number | null; value_status: string; reason_code: string | null }) =>
  metric.value_status !== "defined" ? metric.reason_code :
    percentageMetrics.has(metric.metric_key) ? ratio(metric.value) : decimal(metric.value);

type SuiteStatus = Awaited<ReturnType<typeof api.workspaceSuiteStatus>>;

function SuiteProgress({ suite, chinese }: { suite: SuiteStatus; chinese: boolean }) {
  const completed = suite.status_counts.completed ?? suite.status_counts.accepted ?? 0;
  const failed = suite.status_counts.failed ?? 0;
  const cancelled = suite.status_counts.cancelled ?? 0;
  const running = suite.status_counts.running ?? 0;
  const queued = suite.status_counts.queued ?? 0;
  const percent = suite.total > 0 ? Math.min(100, suite.terminal / suite.total * 100) : 0;
  const title = suite.complete
    ? failed > 0
      ? (chinese ? "实验运行失败" : "Experiment failed")
      : (chinese ? "实验计算已完成" : "Experiment complete")
    : running > 0
      ? (chinese ? "实验正在计算" : "Experiment running")
      : (chinese ? "实验已排队" : "Experiment queued");
  return <>
    <div className="experiment-progress-heading"><strong>{title}</strong><span>{suite.terminal} / {suite.total}</span></div>
    <div
      className={`experiment-progress-track${suite.complete ? " complete" : " running"}`}
      role="progressbar"
      aria-label={chinese ? "回测进度" : "Backtest progress"}
      aria-valuemin={0}
      aria-valuemax={suite.total}
      aria-valuenow={suite.terminal}
    ><i style={{ width: `${percent}%` }} /></div>
    <p className="experiment-progress-detail">
      {chinese
        ? `完成 ${completed} · 运行中 ${running} · 排队 ${queued} · 失败 ${failed} · 取消 ${cancelled}`
        : `Completed ${completed} · Running ${running} · Queued ${queued} · Failed ${failed} · Cancelled ${cancelled}`}
    </p>
  </>;
}

export function ExperimentsPage() {
  const { t, i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage === "zh-CN";
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState("all");
  const [interval, setInterval] = useState("full_history");
  const [frequency, setFrequency] = useState("weekly");
  const [cost, setCost] = useState("5");
  const [rankingMetric, setRankingMetric] = useState("strategy.sharpe_ratio");
  const [resultId, setResultId] = useState(searchParams.get("result") ?? "");
  const [promotionOpen, setPromotionOpen] = useState(false);
  const [productName, setProductName] = useState("");
  const [selectionReason, setSelectionReason] = useState("");
  const [promotionNote, setPromotionNote] = useState("");
  const [page, setPage] = useState(1);
  const detailDialogRef = useRef<HTMLElement>(null);
  const promotionDialogRef = useRef<HTMLElement>(null);
  const submittedSuiteId = searchParams.get("suite") ?? "";
  const pageSize = 50;
  const overview = useQuery({
    queryKey: ["experiments", "overview", submittedSuiteId, status, interval, frequency, cost, rankingMetric, page],
    queryFn: () => api.experimentOverview({
      researchSuiteId: submittedSuiteId || undefined,
      status,
      templateKey: interval,
      frequency: frequency as "weekly" | "monthly",
      costBpsPerSide: Number(cost),
      rankingMetric,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    }),
  });
  const predictiveOverview = useQuery({
    queryKey: ["experiments", "predictive", submittedSuiteId, status, frequency],
    queryFn: () => api.experimentOverview({
      researchSuiteId: submittedSuiteId || undefined,
      status,
      templateKey: "predictive_diagnostic",
      frequency: frequency as "weekly" | "monthly",
      rankingMetric: "predictive.mean_rank_ic",
      limit: 200,
      offset: 0,
    }),
  });
  const submittedSuite = useQuery({
    queryKey: ["workspace", "suite", submittedSuiteId],
    queryFn: () => api.workspaceSuiteStatus(submittedSuiteId),
    enabled: Boolean(submittedSuiteId),
    refetchInterval: (query) => query.state.data?.complete ? false : 2_000,
  });
  useEffect(() => {
    if (!submittedSuite.data?.complete) return;
    void overview.refetch();
    void predictiveOverview.refetch();
  // Refresh the result catalogs once the submitted Suite reaches a terminal state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submittedSuite.data?.complete]);
  const specifications = overview.data?.specifications ?? [];
  const predictiveSpecifications = predictiveOverview.data?.specifications ?? [];
  const pageCount = Math.max(1, Math.ceil((overview.data?.filtered_specification_count ?? 0) / pageSize));
  const visibleSpecifications = specifications;
  const activeResultId = resultId;
  const activeIsPredictive = predictiveOverview.data?.specifications.some(
    (item) => item.result_artifact_id === activeResultId && item.template_key === "predictive_diagnostic",
  ) ?? false;
  const detail = useQuery({
    queryKey: ["experiments", "result", activeResultId],
    queryFn: () => api.experimentResult(activeResultId), enabled: Boolean(activeResultId),
  });
  const qualification = useQuery({
    queryKey: ["experiments", "qualification", activeResultId],
    queryFn: () => api.promotionQualification(activeResultId),
    enabled: Boolean(activeResultId) && !activeIsPredictive, retry: false,
  });
  const promote = useMutation({
    mutationFn: () => api.promoteResult(activeResultId, {
      name: productName, selectionReason, note: promotionNote,
    }),
    onSuccess: () => setPromotionOpen(false),
  });
  const closeDetail = () => {
    setResultId("");
    const next = new URLSearchParams(searchParams);
    next.delete("result");
    setSearchParams(next, { replace: true });
  };
  useEffect(() => {
    if (!resultId) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    detailDialogRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || promotionOpen) return;
      closeDetail();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      previous?.focus();
    };
  // Opening a result establishes one modal interaction session.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultId]);
  useEffect(() => {
    if (!promotionOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    promotionDialogRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPromotionOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      previous?.focus();
    };
  }, [promotionOpen]);

  if (overview.isLoading || predictiveOverview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (predictiveOverview.error) return <ErrorState error={predictiveOverview.error} retry={() => void predictiveOverview.refetch()} />;
  if (!overview.data) return <EmptyState />;
  const data = overview.data;
  const accepted = data.accepted_count;
  const failed = data.failed_count;

  return <div className="page experiment-page">
    <header className="page-heading"><div><p className="eyebrow">SUITE / CELL / ACCEPTED RESULT</p><h1>{t("experiment.title")}</h1><p>{t("experiment.subtitle")}</p></div><QualityBadge state={data.quality.state} /></header>
    <section className="scope-strip experiment-scope-strip">
      <div><span>{t("experiment.suites")}</span><strong>{data.suites.length}</strong></div>
      <div><span>{t("experiment.cells")}</span><strong>{data.total_specification_count}</strong></div>
      <div><span>{t("experiment.accepted")}</span><strong>{accepted}</strong></div>
      <div><span>{t("experiment.failed")}</span><strong>{failed}</strong></div>
    </section>
    {submittedSuiteId && <section className="workspace-release-gate experiment-progress" role="status">
      {submittedSuite.isLoading && <><strong>{chinese ? "实验已提交，正在读取队列状态" : "Experiment submitted; loading queue status"}</strong><span>{submittedSuiteId}</span></>}
      {submittedSuite.error && <><strong>{chinese ? "实验已提交，但暂时无法读取进度" : "Experiment submitted, but progress is temporarily unavailable"}</strong><span>{submittedSuiteId}</span></>}
      {submittedSuite.data && <SuiteProgress suite={submittedSuite.data} chinese={chinese} />}
    </section>}

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">MODEL / TARGET / RANK IC</p><h2>{chinese ? "预测诊断" : "Predictive diagnostics"}</h2></div></div>
      <p className="scope-note">{chinese ? "模型输出在共同有效资产集合内，对冻结目标计算逐期 Rank IC；覆盖率和目标非退化率均由结果门禁复核。" : "Model outputs are evaluated period by period against the frozen target within the common valid-asset set. Result gates verify both coverage and target non-degeneracy."}</p>
      <div className="experiment-table">
        <div className="experiment-table-head"><span>{chinese ? "模型" : "Model"}</span><span>{chinese ? "目标" : "Target"}</span><span>{chinese ? "平均 Rank IC" : "Mean Rank IC"}</span><span>{chinese ? "覆盖率" : "Coverage"}</span><span>{chinese ? "非退化率" : "Nondegenerate"}</span><span>{chinese ? "期数" : "Periods"}</span><span>{t("common.status")}</span></div>
        {predictiveSpecifications.map((item) => <button type="button" className={activeResultId === item.result_artifact_id ? "active" : ""} key={`${item.suite_artifact_id}-${item.artifact_id}`} disabled={!item.result_artifact_id} onClick={() => { if (item.result_artifact_id) { setResultId(item.result_artifact_id); const next = new URLSearchParams(searchParams); next.set("result", item.result_artifact_id); setSearchParams(next, { replace: true }); } }}>
          <strong><ResearchKey value={item.model_specification_key} /><small>{researchLabel(item.frequency, i18n.resolvedLanguage)}</small>{item.status === "failed" && <small className="experiment-failure-summary" title={item.error_summary ?? undefined}>{chinese ? `尝试 ${item.attempt_number ?? "—"} 次` : `Attempt ${item.attempt_number ?? "—"}`} · {item.error_summary ?? (chinese ? "实验单元失败" : "Experiment Cell failed")}</small>}</strong>
          <span><ResearchKey value={item.benchmark_key} /></span>
          <code>{decimal(item.core_metrics["predictive.mean_rank_ic"])}</code><code>{ratio(item.core_metrics["predictive.target_period_coverage"])}</code><code>{ratio(item.core_metrics["predictive.nondegenerate_target_ratio"])}</code><code>{decimal(item.core_metrics["predictive.aligned_target_period_count"])}</code>
          <span className={`experiment-status ${item.status}`}>{item.availability_status === "capacity_rejected" ? researchLabel("capacity_rejected", i18n.resolvedLanguage) : t(`experiment.${item.status}`)}</span>
        </button>)}
      </div>
      {predictiveSpecifications.length === 0 && <p className="factor-empty-note">{t("common.noData")}</p>}
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">COMPARABLE MARKET ASSUMPTIONS</p><h2>{t("experiment.cells")}</h2></div>
        <div className="experiment-filters">
          <label className="experiment-filter">{t("experiment.interval")}<select value={interval} onChange={(event) => { setInterval(event.target.value); setPage(1); }}>{["full_history", "trailing_3_years", "trailing_1_year"].map((key) => <option key={key} value={key}>{researchLabel(key, i18n.resolvedLanguage)}</option>)}</select></label>
          <label className="experiment-filter">{t("experiment.frequency")}<select value={frequency} onChange={(event) => { setFrequency(event.target.value); setPage(1); }}>{["weekly", "monthly"].map((key) => <option key={key} value={key}>{researchLabel(key, i18n.resolvedLanguage)}</option>)}</select></label>
          <label className="experiment-filter">{t("experiment.cost")}<select value={cost} onChange={(event) => { setCost(event.target.value); setPage(1); }}>{["5", "10"].map((value) => <option key={value} value={value}>{value} bps + impact</option>)}</select></label>
          <label className="experiment-filter">{chinese ? "排序指标" : "Ranking metric"}<select value={rankingMetric} onChange={(event) => { setRankingMetric(event.target.value); setPage(1); }}><option value="strategy.sharpe_ratio">{chinese ? "净 Sharpe" : "Net Sharpe"}</option><option value="strategy.cagr">{chinese ? "净 CAGR" : "Net CAGR"}</option><option value="strategy.maximum_drawdown">{chinese ? "最大回撤" : "Maximum drawdown"}</option><option value="relative.annualized_relative_wealth_growth">{chinese ? "相对财富增长" : "Relative wealth growth"}</option></select></label>
          <label className="experiment-filter">{t("common.status")}<select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="all">{t("experiment.all")}</option><option value="accepted">{t("experiment.accepted")}</option><option value="failed">{t("experiment.failed")}</option><option value="pending">{t("experiment.pending")}</option></select></label>
        </div>
      </div>
      <p className="scope-note">{t("experiment.comparisonNote")}</p>
      <div className="experiment-table">
        <div className="experiment-table-head"><span>{t("experiment.strategy")}</span><span>{t("experiment.assumptions")}</span><span>{t("experiment.netCagr")}</span><span>{t("experiment.benchmarkCagr")}</span><span>{t("experiment.sharpe")}</span><span>{t("experiment.drawdown")}</span><span>{t("common.status")}</span></div>
        {visibleSpecifications.map((item) => <button type="button" className={activeResultId === item.result_artifact_id ? "active" : ""} key={`${item.suite_artifact_id}-${item.artifact_id}`} disabled={!item.result_artifact_id} onClick={() => { if (item.result_artifact_id) { setResultId(item.result_artifact_id); const next = new URLSearchParams(searchParams); next.set("result", item.result_artifact_id); setSearchParams(next, { replace: true }); } }}>
          <strong><ResearchKey value={item.model_specification_key} /><small>{item.suite_mode === "exploratory" ? (chinese ? "探索性 · " : "Exploratory · ") : item.suite_mode === "formal" ? (chinese ? "正式 · " : "Formal · ") : (chinese ? "历史 · " : "Legacy · ")}{researchLabel(item.variant_key, i18n.resolvedLanguage)} · {researchLabel(item.frequency, i18n.resolvedLanguage)}</small>{item.status === "failed" && <small className="experiment-failure-summary" title={item.error_summary ?? undefined}>{chinese ? `尝试 ${item.attempt_number ?? "—"} 次` : `Attempt ${item.attempt_number ?? "—"}`} · {item.error_summary ?? (chinese ? "实验单元失败" : "Experiment Cell failed")}</small>}</strong>
          <span><ResearchKey value={item.template_key} /><small>{item.cost_bps_per_side} bps/side · {researchLabel(item.benchmark_key, i18n.resolvedLanguage)}</small></span>
          <code>{ratio(item.core_metrics["strategy.cagr"])}</code><code>{ratio(item.core_metrics["benchmark.cagr"])}</code><code>{decimal(item.core_metrics["strategy.sharpe_ratio"])}</code><code>{ratio(item.core_metrics["strategy.maximum_drawdown"])}</code>
          <span className={`experiment-status ${item.status}`}>{item.availability_status === "capacity_rejected" ? researchLabel("capacity_rejected", i18n.resolvedLanguage) : t(`experiment.${item.status}`)}</span>
        </button>)}
      </div>
      {specifications.length === 0 && <p className="factor-empty-note">{t("common.noData")}</p>}
      {data.filtered_specification_count > pageSize && <nav className="experiment-pagination" aria-label={chinese ? "实验结果分页" : "Experiment result pages"}>
        <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>←</button>
        <span>{page} / {pageCount}<small>{data.filtered_specification_count} {chinese ? "条结果" : "results"}</small></span>
        <button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>→</button>
      </nav>}
    </section>

    {resultId && <div className="experiment-detail-backdrop" onMouseDown={closeDetail}>
      <aside className="experiment-detail-drawer" ref={detailDialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("experiment.detail")} onMouseDown={(event) => event.stopPropagation()}>
    {detail.isLoading && <LoadingState />}
    {detail.error && <ErrorState error={detail.error} retry={() => void detail.refetch()} />}
    {detail.data && <section className="catalog-section experiment-detail">
      <div className="section-heading"><div><p className="eyebrow">IMMUTABLE RESULT AUDIT</p><h2>{t("experiment.detail")}</h2></div><div className="experiment-detail-actions"><Link className="arrow-link" to={`/artifacts/${detail.data.result_artifact_id}`}>{t("experiment.lineage")} →</Link><button type="button" aria-label={chinese ? "关闭实验详情" : "Close experiment detail"} onClick={closeDetail}>×</button></div></div>
      <div className="experiment-detail-strip"><div><span>{t("experiment.resolved")}</span><strong>{detail.data.resolved_start ?? "—"} → {detail.data.resolved_end ?? "—"}</strong></div><div><span>{t("experiment.observations")}</span><strong>{detail.data.observation_count}</strong></div><div><span>{t("experiment.run")}</span><strong>#{detail.data.specification.attempt_number} · {researchLabel(detail.data.run_status, i18n.resolvedLanguage)}</strong></div><div><span>{t("common.quality")}</span><strong>{researchLabel(detail.data.specification.quality_status, i18n.resolvedLanguage)}</strong></div></div>
      {detail.data.specification.template_key !== "predictive_diagnostic" && <div className="experiment-chart-grid">
        <ExperimentChart title={detail.data.specification.availability_status === "capacity_rejected" ? (chinese ? "总财富（容量拒绝前诊断路径）" : "Gross wealth (diagnostic path before capacity rejection)") : (chinese ? "净值曲线 vs SPY 基准" : "Net wealth vs SPY benchmark")} series={detail.data.nav_series} fields={detail.data.specification.availability_status === "capacity_rejected" ? ["strategy_wealth"] : ["strategy_wealth", "benchmark_wealth"]} />
        {detail.data.specification.availability_status !== "capacity_rejected" && <ExperimentChart title={chinese ? "超额净值" : "Excess wealth"} series={detail.data.nav_series} fields={["excess_wealth"]} />}
        <ExperimentChart title={chinese ? "回撤" : "Drawdown"} series={detail.data.nav_series} fields={["drawdown"]} />
      </div>}
      {detail.data.specification.template_key !== "predictive_diagnostic" && <article className="experiment-promotion-card">
        <div><p className="eyebrow">RESEARCH CANDIDATE GATE</p>
          {qualification.isLoading ? <h3>{chinese ? "正在核验升级资格…" : "Checking promotion eligibility…"}</h3> : qualification.error ? <ErrorState error={qualification.error} retry={() => void qualification.refetch()} /> : <>
            <h3>{qualification.data?.eligible ? (chinese ? "可升级为样本外研究候选" : "Eligible for OOS research-candidate promotion") : (chinese ? "暂不可升级为研究候选" : "Not currently eligible for research-candidate promotion")}</h3>
            {!qualification.data?.eligible && <p>{(qualification.data?.reason_codes ?? detail.data.promotion_reason_codes).map((code) => researchLabel(code, i18n.resolvedLanguage)).join(" · ")}</p>}
            {(qualification.data?.warning_codes ?? []).length > 0 && <p className="candidate-warning-count">{chinese ? "研究限制：" : "Research limitations: "}{(qualification.data?.warning_codes ?? []).map((code) => researchLabel(code, i18n.resolvedLanguage)).join(" · ")}</p>}
          </>}
        </div>
        <button type="button" disabled={qualification.isLoading || Boolean(qualification.error) || !qualification.data?.eligible} onClick={() => setPromotionOpen(true)}>{chinese ? "升级为 Product 候选" : "Promote to Product candidate"}</button>
      </article>}
      {promote.data && <p className="scope-note">{chinese ? "Product 候选已激活：" : "Product candidate activated: "}<Link to={`/products/${promote.data.product_enrollment_id}`}>{promote.data.product_enrollment_id}</Link></p>}
      <div className="experiment-detail-grid">
        <article><h3>{t("experiment.metrics")}</h3><div className="experiment-metrics">{detail.data.metrics.map((metric) => <div key={`${metric.series_role}-${metric.metric_key}`}><span>{researchLabel(metric.series_role, i18n.resolvedLanguage)} · {researchLabel(metric.metric_key, i18n.resolvedLanguage)}<code>{metric.metric_key}</code></span><strong>{metricValue(metric)}</strong></div>)}</div></article>
        <article><h3>{t("experiment.checks")}</h3>{detail.data.quality_checks.map((check) => <div className="experiment-audit-row" key={`${check.check_key}-${check.scope_key}`}><span className={`experiment-status ${check.status}`}>{researchLabel(check.status, i18n.resolvedLanguage)}</span><div><ResearchKey value={check.check_key} /><p>{check.message}</p></div></div>)}</article>
        <article><h3>{t("experiment.events")}</h3>{detail.data.events.map((event) => <div className="experiment-audit-row" key={event.sequence_number}><code>{event.sequence_number}</code><div><ResearchKey value={event.event_type} /><p>{event.message}</p></div></div>)}</article>
      </div>
    </section>}
    {detail.data && detail.data.specification.template_key !== "predictive_diagnostic" && <DecisionExplorer resultArtifactId={detail.data.result_artifact_id} />}
      </aside>
    </div>}
    {promotionOpen && <div className="promotion-modal-backdrop" onMouseDown={() => setPromotionOpen(false)}>
      <section className="promotion-modal" ref={promotionDialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={chinese ? "升级为 Product 研究候选" : "Promote to Product candidate"} onMouseDown={(event) => event.stopPropagation()}>
        <div className="section-heading"><div><p className="eyebrow">MANUAL PROMOTION</p><h2>{chinese ? "升级为 Product 研究候选" : "Promote to Product research candidate"}</h2></div><button type="button" aria-label={chinese ? "关闭升级弹窗" : "Close promotion dialog"} onClick={() => setPromotionOpen(false)}>×</button></div>
        <label>{chinese ? "候选名称" : "Candidate name"}<input value={productName} onChange={(event) => setProductName(event.target.value)} /></label>
        <label>{chinese ? "选择理由" : "Selection reason"}<textarea value={selectionReason} onChange={(event) => setSelectionReason(event.target.value)} /></label>
        <label>{chinese ? "备注（可选）" : "Note (optional)"}<textarea value={promotionNote} onChange={(event) => setPromotionNote(event.target.value)} /></label>
        {promote.error && <ErrorState error={promote.error} retry={() => promote.mutate()} />}
        <footer><button type="button" onClick={() => setPromotionOpen(false)}>{chinese ? "取消" : "Cancel"}</button><button type="button" disabled={!productName.trim() || !selectionReason.trim() || promote.isPending} onClick={() => promote.mutate()}>{chinese ? "确认升级并开始样本外观察" : "Confirm promotion and start OOS observation"}</button></footer>
      </section>
    </div>}
  </div>;
}

type NavPoint = Awaited<ReturnType<typeof api.experimentResult>>["nav_series"][number];
type NavField = "strategy_wealth" | "benchmark_wealth" | "excess_wealth" | "drawdown";

function ExperimentChart({ title, series, fields }: { title: string; series: NavPoint[]; fields: NavField[] }) {
  const { i18n } = useTranslation();
  if (series.length < 2) return <article className="experiment-chart"><h3>{title}</h3><p>{i18n.resolvedLanguage === "en" ? "No published path." : "暂无已发布路径。"}</p></article>;
  const values = series.flatMap((point) => fields.map((field) => point[field]));
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  const paths = fields.map((field) => series.map((point, index) => {
    const x = index / (series.length - 1) * 100;
    const y = 42 - ((point[field] - minimum) / span) * 38;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" "));
  return <article className="experiment-chart"><h3>{title}</h3><svg viewBox="0 0 100 46" role="img" aria-label={title}>{paths.map((path, index) => <polyline key={fields[index]} className={`series-${index}`} points={path} />)}</svg><footer><span>{series[0].nav_date}</span><span>{series.at(-1)?.nav_date}</span></footer></article>;
}
