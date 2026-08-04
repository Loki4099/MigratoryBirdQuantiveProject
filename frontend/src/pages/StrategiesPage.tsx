import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

const percent = (input: number) => new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(input);
const number = (input: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(input);

export function StrategiesPage() {
  const { t } = useTranslation();
  const overview = useQuery({ queryKey: ["strategies", "overview"], queryFn: api.strategyOverview });
  const [pathId, setPathId] = useState("");
  const [decisionDate, setDecisionDate] = useState("");

  const activePathId = pathId || overview.data?.target_paths[0]?.artifact_id || "";
  const detail = useQuery({ queryKey: ["strategies", "target", activePathId], queryFn: () => api.strategyTargetPath(activePathId), enabled: Boolean(activePathId) });
  const activeDecisionDate = detail.data?.decisions.some((item) => item.decision_date === decisionDate)
    ? decisionDate : detail.data?.decisions[0]?.decision_date ?? "";
  const decision = useMemo(() => detail.data?.decisions.find((item) => item.decision_date === activeDecisionDate), [detail.data, activeDecisionDate]);

  if (overview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (!overview.data) return <EmptyState />;
  const data = overview.data;

  return <div className="page strategy-page">
    <header className="page-heading"><div><p className="eyebrow">RULE / PRODUCT / TARGET PATH</p><h1>{t("strategy.title")}</h1><p>{t("strategy.subtitle")}</p></div><QualityBadge state={data.quality.state} /></header>
    <section className="scope-strip strategy-scope-strip">
      <div><span>{t("strategy.rules")}</span><strong>{data.rules.strategy_key} · v{data.rules.version_number}</strong></div>
      <div><span>{t("strategy.variants")}</span><strong>{data.rules.variants.length}</strong></div>
      <div><span>{t("strategy.products")}</span><strong>{data.products.length}</strong></div>
      <div><span>{t("strategy.targetPaths")}</span><strong>{data.target_paths.length}</strong></div>
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">FROZEN DECISION SEMANTICS</p><h2>{t("strategy.rules")}</h2></div><Link className="arrow-link" to={`/artifacts/${data.rules.version_artifact_id}`}>{t("strategy.lineage")} →</Link></div>
      <div className="strategy-rule-summary">
        <article><span>{t("strategy.hypothesis")}</span><strong>{data.rules.strategy_family}</strong><p>{data.rules.hypothesis}</p></article>
        <article><span>{t("strategy.compatibleOutputs")}</span>{data.rules.compatible_model_output_types.map((item) => <code key={item}>{item}</code>)}</article>
        <article><span>{t("strategy.execution")}</span><strong>{data.rules.execution_policy.policy_key}</strong><p>T+{data.rules.execution_policy.delay_common_sessions} · {data.rules.execution_policy.execution_price}</p></article>
      </div>
      <p className="scope-note">{t("strategy.rulesNote")}</p>
      <div className="strategy-variant-grid">{data.rules.variants.map((variant) => <article key={variant.artifact_id}>
        <header><div><span>{variant.research_tier}</span><strong>{variant.variant_key}</strong></div><b>K={variant.target_k}</b></header>
        <dl><div><dt>{t("strategy.selectionOrder")}</dt><dd>{variant.selection_order}</dd></div><div><dt>{t("strategy.trend")}</dt><dd>{variant.trend_filter}</dd></div><div><dt>{t("strategy.emptySlots")}</dt><dd>{variant.empty_slot_policy}</dd></div><div><dt>{t("strategy.tiePolicy")}</dt><dd>{variant.tie_policy}</dd></div></dl>
        {variant.auxiliary_signal_key && <code>{variant.auxiliary_signal_key}</code>}
      </article>)}</div>
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">MODEL × RULE × UNIVERSE × SCHEDULE</p><h2>{t("strategy.products")}</h2></div></div>
      {data.products.length === 0 ? <p className="factor-empty-note">{t("strategy.noProducts")}</p> : <div className="strategy-product-list">{data.products.map((product) => <article key={product.artifact_id}>
        <div><span>{product.frequency} · K={product.target_k} · {product.research_tier}</span><strong>{product.model_specification_key}</strong><code>{product.variant_key}</code></div>
        <dl><div><dt>{t("strategy.universe")}</dt><dd>{product.universe_key}</dd></div><div><dt>{t("strategy.execution")}</dt><dd>{product.execution_price}</dd></div><div><dt>{t("strategy.targetCount")}</dt><dd>{product.target_path_count}</dd></div></dl>
        <Link aria-label={t("strategy.lineage")} to={`/artifacts/${product.artifact_id}`}>→</Link>
      </article>)}</div>}
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">PUBLISHED PORTFOLIO DECISIONS</p><h2>{t("strategy.targetPaths")}</h2></div></div>
      <p className="scope-note">{t("strategy.targetNote")}</p>
      {data.target_paths.length === 0 ? <p className="factor-empty-note">{t("strategy.noPaths")}</p> : <>
        <div className="strategy-target-controls">
          <label>{t("strategy.selectPath")}<select value={activePathId} onChange={(event) => { setPathId(event.target.value); setDecisionDate(""); }}>{data.target_paths.map((path) => <option key={path.artifact_id} value={path.artifact_id}>{path.frequency} · {path.variant_key} · {path.model_specification_key}</option>)}</select></label>
          <label>{t("strategy.selectDate")}<select value={activeDecisionDate} onChange={(event) => setDecisionDate(event.target.value)} disabled={!detail.data}>{detail.data?.decisions.map((item) => <option key={item.decision_date}>{item.decision_date}</option>)}</select></label>
        </div>
        {detail.isLoading && <LoadingState />}
        {detail.error && <ErrorState error={detail.error} retry={() => void detail.refetch()} />}
        {detail.data && decision && <div className="strategy-decision">
          <header><div><span>{t("strategy.coverage")}</span><strong>{detail.data.target_path.coverage_start} → {detail.data.target_path.coverage_end}</strong></div><div><span>{t("strategy.holdings")}</span><strong>{decision.actual_holding_count} / K={decision.target_k}</strong></div><div><span>{t("strategy.boundaryTies")}</span><strong>{decision.boundary_tie_count}</strong></div><div><span>{t("strategy.reserve")}</span><strong>{percent(decision.reserve_target_weight)}</strong></div></header>
          <div className="strategy-position-table"><div className="strategy-position-head"><span>{t("strategy.asset")}</span><span>{t("strategy.score")}</span><span>{t("strategy.rank")}</span><span>{t("strategy.trend")}</span><span>{t("strategy.eligible")}</span><span>{t("strategy.weight")}</span><span>{t("strategy.reason")}</span></div>{decision.positions.map((position) => <div className={position.selected ? "selected" : ""} key={position.asset_key}><strong>{position.symbol}<small>{position.asset_key}</small></strong><code>{number(position.model_score)}</code><code>{number(position.model_rank)}</code><span>{position.trend_state ?? "—"}</span><span>{position.strategy_eligible ? t("strategy.yes") : t("strategy.no")}</span><code>{percent(position.target_weight)}</code><span>{position.decision_reason}</span></div>)}</div>
          <Link className="arrow-link" to={`/artifacts/${detail.data.target_path.artifact_id}`}>{t("strategy.lineage")} →</Link>
        </div>}
      </>}
    </section>
  </div>;
}
