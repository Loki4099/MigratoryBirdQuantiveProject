import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

const percentageMetrics = new Set([
  "cumulative_return", "cagr", "annualized_volatility", "maximum_drawdown",
  "cumulative_relative_return", "annualized_relative_wealth_growth", "cagr_spread",
  "tracking_error", "annualized_alpha",
]);

function displayMetric(metric: { metric_key: string; value: number | null; value_status: string; reason_code: string | null }) {
  if (metric.value_status !== "defined" || metric.value == null) return metric.reason_code ?? "undefined";
  return new Intl.NumberFormat(undefined, percentageMetrics.has(metric.metric_key)
    ? { style: "percent", maximumFractionDigits: 2 }
    : { maximumFractionDigits: 3 }).format(metric.value);
}

export function ComparePage() {
  const { t } = useTranslation();
  const overview = useQuery({ queryKey: ["experiments", "overview"], queryFn: api.experimentOverview });
  const candidates = useMemo(() => {
    const seen = new Set<string>();
    return (overview.data?.specifications ?? []).filter((item) => {
      if (item.status !== "accepted" || !item.result_artifact_id || seen.has(item.result_artifact_id)) return false;
      seen.add(item.result_artifact_id);
      return true;
    });
  }, [overview.data]);
  const [selection, setSelection] = useState<string[]>([]);
  const active = selection.length ? selection : candidates.slice(0, 2).map((item) => item.result_artifact_id as string);
  const comparison = useQuery({
    queryKey: ["compare", ...active], queryFn: () => api.productCompare(active),
    enabled: active.length >= 2,
  });
  const metricOptions = comparison.data?.entries[0]?.metrics ?? [];
  const [metricKey, setMetricKey] = useState("strategy.cagr");

  if (overview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (!overview.data) return <EmptyState />;

  function toggle(resultId: string) {
    const current = active.includes(resultId) ? active.filter((item) => item !== resultId) : [...active, resultId];
    if (current.length <= 6) setSelection(current);
  }

  return <div className="page compare-page">
    <header className="page-heading"><div><p className="eyebrow">PRODUCT / CONTROLLED VIEW</p><h1>{t("compare.title")}</h1><p>{t("compare.subtitle")}</p></div>{comparison.data && <QualityBadge state={comparison.data.quality.state} />}</header>
    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">ACCEPTED RESULTS</p><h2>{t("compare.select")}</h2></div><span className="model-result-count">{active.length} / 6</span></div>
      <div className="compare-selector">{candidates.map((item) => <label key={item.result_artifact_id}><input type="checkbox" checked={active.includes(item.result_artifact_id as string)} onChange={() => toggle(item.result_artifact_id as string)} /><span><strong>{item.model_specification_key}</strong><small>{item.variant_key} · {item.frequency} · {item.cost_bps_per_side} bps · {item.template_key}</small></span></label>)}</div>
      {candidates.length < 2 && <p className="factor-empty-note">{t("compare.needTwo")}</p>}
    </section>
    {comparison.isLoading && <LoadingState />}
    {comparison.error && <ErrorState error={comparison.error} retry={() => void comparison.refetch()} />}
    {comparison.data && <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">COMPARISON CLASSIFICATION</p><h2>{t(`compare.${comparison.data.mode}`)}</h2></div><label className="experiment-filter">{t("compare.metric")}<select value={metricKey} onChange={(event) => setMetricKey(event.target.value)}>{metricOptions.map((metric) => <option key={`${metric.series_role}.${metric.metric_key}`} value={`${metric.series_role}.${metric.metric_key}`}>{metric.series_role} · {metric.name}</option>)}</select></label></div>
      <p className={`compare-verdict ${comparison.data.mode}`}>{comparison.data.mode === "controlled" ? t("compare.controlledNote", { dimension: comparison.data.changed_dimensions.join(", ") }) : comparison.data.mode === "identical" ? t("compare.identicalNote") : t("compare.sideNote")}</p>
      {comparison.data.blocking_context_fields.length > 0 && <p className="scope-note">{t("compare.blockers")}: <code>{comparison.data.blocking_context_fields.join(", ")}</code></p>}
      <div className="compare-grid">{comparison.data.entries.map((entry) => {
        const metric = entry.metrics.find((item) => `${item.series_role}.${item.metric_key}` === metricKey) ?? entry.metrics[0];
        return <article key={entry.result_artifact_id}><span>{entry.template_key} · {entry.cost_bps_per_side} bps</span><h3>{entry.model_specification_key}</h3><p>{entry.variant_key} · K={entry.target_k} · {entry.frequency}</p><strong>{metric ? displayMetric(metric) : "—"}</strong><small>{metric?.name ?? t("common.noData")} · {entry.resolved_start ?? "—"} → {entry.resolved_end ?? "—"}</small><Link className="arrow-link" to={`/experiments?result=${entry.result_artifact_id}`}>{t("compare.open")} →</Link></article>;
      })}</div>
      <p className="scope-note">{t("compare.disclaimer")}</p>
    </section>}
  </div>;
}
