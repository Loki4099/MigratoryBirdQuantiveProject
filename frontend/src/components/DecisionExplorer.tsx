import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "./QueryState";

const number = (value: number | null | undefined) => value == null ? "—" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
const percent = (value: number | null | undefined) => value == null ? "—" : new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value);

export function DecisionExplorer({ resultArtifactId }: { resultArtifactId: string }) {
  const { t } = useTranslation();
  const [decisionDate, setDecisionDate] = useState("");
  const query = useQuery({
    queryKey: ["decision-explorer", resultArtifactId, decisionDate],
    queryFn: () => api.decisionExplorer(resultArtifactId, decisionDate),
  });
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  if (!query.data) return <EmptyState />;
  const data = query.data;
  return <section className="catalog-section decision-explorer">
    <div className="section-heading"><div><p className="eyebrow">STRATEGY → MODEL → SIGNAL → FACTOR → DATA</p><h2>{t("decision.title")}</h2></div><label className="experiment-filter">{t("decision.date")}<select value={data.selected_date} onChange={(event) => setDecisionDate(event.target.value)}>{data.available_dates.map((item) => <option key={item}>{item}</option>)}</select></label></div>
    <p className="scope-note">{t("decision.note")}</p>
    <div className="decision-context"><div><span>{t("decision.method")}</span><strong>{data.model_method_key}</strong></div><div><span>{t("decision.holdings")}</span><strong>{data.actual_holding_count} / K={data.target_k}</strong></div><div><span>{t("decision.reserve")}</span><strong>{percent(data.reserve_target_weight)}</strong></div><div><span>{t("decision.data")}</span><Link to={`/artifacts/${data.data_bundle_artifact_id}`}>{String(data.data_bundle_artifact_id).slice(0, 8)}…</Link></div></div>
    <div className="decision-assets">{data.positions.map((position) => <article className={position.selected ? "selected" : ""} key={position.asset_key}><header><div><span>{position.symbol}</span><strong>{position.asset_key}</strong></div><dl><div><dt>{t("decision.score")}</dt><dd>{number(position.model_score)}</dd></div><div><dt>{t("decision.rank")}</dt><dd>{number(position.model_rank)}</dd></div><div><dt>{t("decision.weight")}</dt><dd>{percent(position.target_weight)}</dd></div></dl></header><p>{position.decision_reason} · {position.trend_state ?? "no trend filter"}</p><details open={position.selected}><summary>{t("decision.components")} ({position.components.length})</summary><div className="decision-components">{position.components.map((component) => <div key={`${position.asset_key}-${component.signal_key}`}><span><strong>{component.dimension_key}</strong><small>{component.signal_key}</small><small>{component.factor_variant_key}</small></span><code>{number(component.factor_value)}</code><code>{number(component.signal_score)}</code><code>{component.overall_contribution == null ? t("decision.notExact") : number(component.overall_contribution)}</code><Link to={`/artifacts/${component.signal_dataset_artifact_id}`}>→</Link></div>)}</div></details></article>)}</div>
    <div className="decision-legend"><span>{t("decision.factorValue")}</span><span>{t("decision.signalScore")}</span><span>{t("decision.contribution")}</span></div>
  </section>;
}
