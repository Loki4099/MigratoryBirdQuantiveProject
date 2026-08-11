import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "./QueryState";
import { ResearchKey, researchLabel } from "./ResearchText";

const percent = (value: number | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value);
const decimal = (value: number | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value);

export function ProductRanking({ compact = false }: { compact?: boolean }) {
  const { t, i18n } = useTranslation();
  const [metric, setMetric] = useState("net_sharpe");
  const [cohortId, setCohortId] = useState("");
  const ranking = useQuery({
    queryKey: ["product-ranking", metric, cohortId],
    queryFn: () => api.productRanking(metric, cohortId),
  });
  if (ranking.isLoading) return <LoadingState />;
  if (ranking.error) return <ErrorState error={ranking.error} retry={() => void ranking.refetch()} />;
  if (!ranking.data || ranking.data.cohorts.length === 0) return <EmptyState />;
  const data = ranking.data;
  const active = data.cohorts.find((item) => item.artifact_id === data.active_cohort_artifact_id);
  const entries = compact ? data.entries.slice(0, 5) : data.entries;
  const selectedIsPercent = ["net_cagr", "relative_wealth_growth", "maximum_drawdown"].includes(metric);

  return <section className={`product-ranking ${compact ? "compact" : ""}`}>
    <div className="section-heading"><div><p className="eyebrow">STRICT COMPARISON COHORT</p><h2>{t("ranking.title")}</h2></div>
      {!compact && <div className="ranking-controls">
        <label>{t("ranking.cohort")}<select value={data.active_cohort_artifact_id ?? ""} onChange={(event) => setCohortId(event.target.value)}>{data.cohorts.map((cohort) => <option key={cohort.artifact_id} value={cohort.artifact_id}>{researchLabel(cohort.template_key, i18n.resolvedLanguage)} · K={cohort.target_k} · {researchLabel(cohort.frequency, i18n.resolvedLanguage)} · {cohort.cost_bps_per_side} bps</option>)}</select></label>
        <label>{t("ranking.metric")}<select value={metric} onChange={(event) => setMetric(event.target.value)}><option value="net_sharpe">{t("ranking.netSharpe")}</option><option value="net_cagr">{t("ranking.netCagr")}</option><option value="relative_wealth_growth">{t("ranking.relativeGrowth")}</option><option value="maximum_drawdown">{t("ranking.drawdown")}</option><option value="calmar">{t("ranking.calmar")}</option></select></label>
      </div>}
    </div>
    {active && <div className="ranking-context"><div><span>{t("ranking.context")}</span><strong>{researchLabel(active.template_key, i18n.resolvedLanguage)} · K={active.target_k} · {researchLabel(active.frequency, i18n.resolvedLanguage)}</strong></div><div><span>{t("ranking.market")}</span><strong>{researchLabel(active.benchmark_key, i18n.resolvedLanguage)} · {active.cost_bps_per_side} bps/side · {active.currency}</strong></div><div><span>{t("ranking.warmup")}</span><strong>{active.required_warmup_observations} · {active.common_data_ready_date}</strong></div><div><span>{t("ranking.candidates")}</span><strong>{data.ranked_count} / {data.candidate_count}</strong></div></div>}
    <p className="scope-note">{t("ranking.disclaimer")}</p>
    <div className="ranking-table">
      <div className="ranking-head"><span>{t("ranking.rank")}</span><span>{t("ranking.product")}</span><span>{t("ranking.configuration")}</span><span>{t("ranking.selectedMetric")}</span><span>{t("ranking.netCagr")}</span><span>{t("ranking.relativeGrowth")}</span><span>{t("ranking.drawdown")}</span>{!compact && <span />}</div>
      {entries.map((entry) => <div key={entry.result_artifact_id}>
        <strong className="ranking-number">{entry.rank ?? "—"}</strong>
        <span><strong><ResearchKey value={entry.model_specification_key} /></strong><small>{entry.product_key}</small></span>
        <span><ResearchKey value={entry.variant_key} /><small>K={entry.target_k} · {researchLabel(entry.frequency, i18n.resolvedLanguage)}</small></span>
        <code>{entry.value_status === "defined" ? (selectedIsPercent ? percent(entry.metric_value) : decimal(entry.metric_value)) : entry.reason_code}</code>
        <code>{percent(entry.core_metrics["strategy.cagr"])}</code>
        <code>{percent(entry.core_metrics["relative.annualized_relative_wealth_growth"])}</code>
        <code>{percent(entry.core_metrics["strategy.maximum_drawdown"])}</code>
        {!compact && <Link aria-label={t("ranking.openResult")} to={`/artifacts/${entry.result_artifact_id}`}>→</Link>}
      </div>)}
    </div>
  </section>;
}
