import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { ProductRanking } from "../components/ProductRanking";
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

export function ExperimentsPage() {
  const { t, i18n } = useTranslation();
  const overview = useQuery({ queryKey: ["experiments", "overview"], queryFn: api.experimentOverview });
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState("all");
  const [resultId, setResultId] = useState(searchParams.get("result") ?? "");
  const specifications = useMemo(() => overview.data?.specifications.filter(
    (item) => status === "all" || item.status === status,
  ) ?? [], [overview.data, status]);
  const activeResultId = resultId || specifications.find((item) => item.result_artifact_id)?.result_artifact_id || "";
  const detail = useQuery({
    queryKey: ["experiments", "result", activeResultId],
    queryFn: () => api.experimentResult(activeResultId), enabled: Boolean(activeResultId),
  });

  if (overview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (!overview.data) return <EmptyState />;
  const data = overview.data;
  const accepted = data.specifications.filter((item) => item.status === "accepted").length;
  const failed = data.specifications.filter((item) => item.status === "failed").length;

  return <div className="page experiment-page">
    <header className="page-heading"><div><p className="eyebrow">SUITE / CELL / ACCEPTED RESULT</p><h1>{t("experiment.title")}</h1><p>{t("experiment.subtitle")}</p></div><QualityBadge state={data.quality.state} /></header>
    <section className="scope-strip experiment-scope-strip">
      <div><span>{t("experiment.suites")}</span><strong>{data.suites.length}</strong></div>
      <div><span>{t("experiment.cells")}</span><strong>{data.specifications.length}</strong></div>
      <div><span>{t("experiment.accepted")}</span><strong>{accepted}</strong></div>
      <div><span>{t("experiment.failed")}</span><strong>{failed}</strong></div>
    </section>

    <ProductRanking />

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">COMPARABLE MARKET ASSUMPTIONS</p><h2>{t("experiment.cells")}</h2></div>
        <label className="experiment-filter">{t("common.status")}<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">{t("experiment.all")}</option><option value="accepted">{t("experiment.accepted")}</option><option value="failed">{t("experiment.failed")}</option><option value="pending">{t("experiment.pending")}</option></select></label>
      </div>
      <p className="scope-note">{t("experiment.comparisonNote")}</p>
      <div className="experiment-table">
        <div className="experiment-table-head"><span>{t("experiment.strategy")}</span><span>{t("experiment.assumptions")}</span><span>{t("experiment.netCagr")}</span><span>{t("experiment.benchmarkCagr")}</span><span>{t("experiment.sharpe")}</span><span>{t("experiment.drawdown")}</span><span>{t("common.status")}</span></div>
        {specifications.map((item) => <button type="button" className={activeResultId === item.result_artifact_id ? "active" : ""} key={`${item.suite_artifact_id}-${item.artifact_id}`} disabled={!item.result_artifact_id} onClick={() => { if (item.result_artifact_id) { setResultId(item.result_artifact_id); const next = new URLSearchParams(searchParams); next.set("result", item.result_artifact_id); setSearchParams(next, { replace: true }); } }}>
          <strong><ResearchKey value={item.model_specification_key} /><small>{researchLabel(item.variant_key, i18n.resolvedLanguage)} · {researchLabel(item.frequency, i18n.resolvedLanguage)}</small></strong>
          <span><ResearchKey value={item.template_key} /><small>{item.cost_bps_per_side} bps/side · {researchLabel(item.benchmark_key, i18n.resolvedLanguage)}</small></span>
          <code>{ratio(item.core_metrics["strategy.cagr"])}</code><code>{ratio(item.core_metrics["benchmark.cagr"])}</code><code>{decimal(item.core_metrics["strategy.sharpe_ratio"])}</code><code>{ratio(item.core_metrics["strategy.maximum_drawdown"])}</code>
          <span className={`experiment-status ${item.status}`}>{t(`experiment.${item.status}`)}</span>
        </button>)}
      </div>
      {specifications.length === 0 && <p className="factor-empty-note">{t("common.noData")}</p>}
    </section>

    {detail.isLoading && <LoadingState />}
    {detail.error && <ErrorState error={detail.error} retry={() => void detail.refetch()} />}
    {detail.data && <section className="catalog-section experiment-detail">
      <div className="section-heading"><div><p className="eyebrow">IMMUTABLE RESULT AUDIT</p><h2>{t("experiment.detail")}</h2></div><Link className="arrow-link" to={`/artifacts/${detail.data.result_artifact_id}`}>{t("experiment.lineage")} →</Link></div>
      <div className="experiment-detail-strip"><div><span>{t("experiment.resolved")}</span><strong>{detail.data.resolved_start ?? "—"} → {detail.data.resolved_end ?? "—"}</strong></div><div><span>{t("experiment.observations")}</span><strong>{detail.data.observation_count}</strong></div><div><span>{t("experiment.run")}</span><strong>#{detail.data.specification.attempt_number} · {researchLabel(detail.data.run_status, i18n.resolvedLanguage)}</strong></div><div><span>{t("common.quality")}</span><strong>{researchLabel(detail.data.specification.quality_status, i18n.resolvedLanguage)}</strong></div></div>
      <div className="experiment-detail-grid">
        <article><h3>{t("experiment.metrics")}</h3><div className="experiment-metrics">{detail.data.metrics.map((metric) => <div key={`${metric.series_role}-${metric.metric_key}`}><span>{researchLabel(metric.series_role, i18n.resolvedLanguage)} · {researchLabel(metric.metric_key, i18n.resolvedLanguage)}<code>{metric.metric_key}</code></span><strong>{metricValue(metric)}</strong></div>)}</div></article>
        <article><h3>{t("experiment.checks")}</h3>{detail.data.quality_checks.map((check) => <div className="experiment-audit-row" key={`${check.check_key}-${check.scope_key}`}><span className={`experiment-status ${check.status}`}>{researchLabel(check.status, i18n.resolvedLanguage)}</span><div><ResearchKey value={check.check_key} /><p>{check.message}</p></div></div>)}</article>
        <article><h3>{t("experiment.events")}</h3>{detail.data.events.map((event) => <div className="experiment-audit-row" key={event.sequence_number}><code>{event.sequence_number}</code><div><ResearchKey value={event.event_type} /><p>{event.message}</p></div></div>)}</article>
      </div>
    </section>}
    {detail.data && <DecisionExplorer resultArtifactId={detail.data.result_artifact_id} />}
  </div>;
}
