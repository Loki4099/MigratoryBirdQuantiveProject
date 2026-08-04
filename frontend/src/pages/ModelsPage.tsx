import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

type Frequency = "weekly" | "monthly";
type ModelType = "all" | "single_signal" | "dimension_subset_equal_weight" | "fixed_weight" | "directional_vote";

function value(input: number | null, digits = 3) {
  if (input === null) return "—";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(input);
}

function percent(input: number | null, digits = 1) {
  if (input === null) return "—";
  return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: digits }).format(input);
}

export function ModelsPage() {
  const { t } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const [modelType, setModelType] = useState<ModelType>("all");
  const [term, setTerm] = useState("");
  const [visibleCount, setVisibleCount] = useState(20);
  const frequency: Frequency = search.get("frequency") === "monthly" ? "monthly" : "weekly";
  const overview = useQuery({
    queryKey: ["models", "overview", frequency],
    queryFn: () => api.modelOverview(frequency),
  });

  const models = useMemo(() => {
    const normalized = term.trim().toLowerCase();
    return (overview.data?.models ?? []).filter((model) => {
      if (modelType !== "all" && model.specification_type !== modelType) return false;
      if (!normalized) return true;
      return [
        model.specification_key,
        model.overall_method_key,
        ...model.dimensions.map((dimension) => dimension.dimension_key),
      ].some((item) => item.toLowerCase().includes(normalized));
    });
  }, [overview.data, modelType, term]);

  function selectFrequency(next: Frequency) {
    const updated = new URLSearchParams(search);
    updated.set("frequency", next);
    setSearch(updated, { replace: true });
  }

  if (overview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (!overview.data) return <EmptyState />;

  const data = overview.data;
  const highPairs = data.pairs.filter((item) => item.high_correlation);
  const fullAblations = data.ablations.filter((item) => item.window_key === "full");
  const types: ModelType[] = ["all", "single_signal", "dimension_subset_equal_weight", "fixed_weight", "directional_vote"];

  return <div className="page">
    <header className="page-heading">
      <div>
        <p className="eyebrow">MODEL / COMPOSITION / ABLATION</p>
        <h1>{t("model.title")}</h1>
        <p>{t("model.subtitle")}</p>
      </div>
      <QualityBadge state={data.quality.state} />
    </header>

    <div className="signal-frequency" aria-label={t("model.frequency")}>
      {(["weekly", "monthly"] as const).map((item) => <button
        className={frequency === item ? "active" : ""}
        key={item}
        onClick={() => selectFrequency(item)}
        type="button"
      >{t(`model.${item}`)}</button>)}
    </div>

    <section className="scope-strip model-scope-strip">
      <div><span>{t("model.context")}</span><strong>{data.coverage_start} → {data.coverage_end}</strong></div>
      <div><span>{t("model.target")}</span><strong>{data.target_key}</strong></div>
      <div><span>{t("model.periods")}</span><strong>{data.common_period_count}</strong></div>
      <div><span>{t("model.models")}</span><strong>{data.model_count}</strong></div>
      <div><span>{t("model.ablations")}</span><strong>{fullAblations.length}</strong></div>
    </section>
    <p className="scope-note">{t("model.sampleNote")}</p>

    <section className="catalog-section">
      <div className="section-heading">
        <div><p className="eyebrow">DEFINITION / DIMENSION / SIGNAL</p><h2>{t("model.library")}</h2></div>
        <Link className="arrow-link" to={`/artifacts/${data.evaluation_artifact_id}`}>{t("model.lineage")} →</Link>
      </div>
      <div className="model-toolbar">
        <div>{types.map((item) => <button className={modelType === item ? "active" : ""} key={item} onClick={() => { setModelType(item); setVisibleCount(20); }} type="button">{t(`model.type.${item}`)}</button>)}</div>
        <input aria-label={t("model.search")} onChange={(event) => { setTerm(event.target.value); setVisibleCount(20); }} placeholder={t("model.search")} type="search" value={term} />
      </div>
      <p className="model-result-count">{t("model.showing", { count: Math.min(visibleCount, models.length), total: models.length })}</p>
      <div className="model-grid">
        {models.slice(0, visibleCount).map((model) => <article className="model-card" key={model.model_dataset_artifact_id}>
          <div className="signal-card-heading">
            <div><span>{model.specification_type} · {model.research_tier}</span><strong>{model.specification_key}</strong></div>
            <QualityBadge state={model.quality.state} />
          </div>
          <p>{model.hypothesis}</p>
          <div className="signal-tags">
            <code>{model.overall_method_key}</code><code>{model.output_type}</code>
            <code>{model.active_dimension_count}D / {model.component_count}S</code>
          </div>
          <div className="signal-stat-grid">
            <span>{t("model.rankIc")}<strong>{value(model.full.mean_rank_ic)}</strong></span>
            <span>{t("model.positiveIc")}<strong>{percent(model.full.positive_ic_ratio)}</strong></span>
            <span>{t("model.topBottom")}<strong>{percent(model.full.mean_top_bottom_spread, 2)}</strong></span>
            <span>{t("model.informationRatio")}<strong>{value(model.full.information_ratio)}</strong></span>
            <span>{t("model.dispersion")}<strong>{value(model.full.mean_score_dispersion)}</strong></span>
            <span>{t("model.turnover")}<strong>{percent(model.full.mean_top2_turnover)}</strong></span>
          </div>
          <details className="model-composition">
            <summary>{t("model.composition")} · {model.dimensions.length}</summary>
            {model.dimensions.map((dimension) => <div key={dimension.dimension_key}>
              <header><strong>{dimension.dimension_key}</strong><code>{percent(dimension.weight)} · {dimension.method_key}</code></header>
              <ul>{dimension.components.map((component) => <li key={component.signal_key}><span>{component.signal_key}</span><code>{percent(component.weight)}</code></li>)}</ul>
            </div>)}
          </details>
          <details className="signal-stability">
            <summary>{t("model.stability")} · {model.stability.length}</summary>
            {model.stability.length === 0 ? <p>{t("model.noStability")}</p> : model.stability.map((window) => <div key={window.window_key}>
              <code>{window.window_key.replace("year:", "")}</code><span>IC {value(window.mean_rank_ic)}</span><span>+IC {percent(window.positive_ic_ratio)}</span><span>T−B {percent(window.mean_top_bottom_spread, 2)}</span><small>n={window.period_count}</small>
            </div>)}
          </details>
        </article>)}
      </div>
      {visibleCount < models.length && <button className="model-load-more" onClick={() => setVisibleCount((count) => count + 20)} type="button">{t("model.loadMore")}</button>}
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">CONTROLLED REMOVAL / FULL WINDOW</p><h2>{t("model.ablationTitle")}</h2></div></div>
      <p className="scope-note">{t("model.ablationNote")}</p>
      <details className="ablation-disclosure">
        <summary>{t("model.openAblations", { count: fullAblations.length })}</summary>
        <div className="ablation-list">{fullAblations.map((item) => <article key={`${item.full_specification_key}-${item.removed_dimension_key}`}>
          <div><strong>{item.full_specification_key}</strong><span>− {item.removed_dimension_key} → {item.ablated_specification_key}</span></div>
          <dl><div><dt>Δ IC</dt><dd>{value(item.delta_mean_rank_ic)}</dd></div><div><dt>Δ IC IR</dt><dd>{value(item.delta_information_ratio)}</dd></div><div><dt>Δ T−B</dt><dd>{percent(item.delta_mean_top_bottom_spread, 2)}</dd></div></dl>
        </article>)}</div>
      </details>
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">|ρ| ≥ {data.high_correlation_threshold}</p><h2>{t("model.redundancy")}</h2></div></div>
      <p className="scope-note">{t("model.redundancyNote")}</p>
      {highPairs.length === 0 ? <p className="factor-empty-note">{t("model.noHighPairs")}</p> : <div className="signal-pair-list">{highPairs.slice(0, 30).map((pair) => <article key={`${pair.left_specification_key}-${pair.right_specification_key}`}>
        <div><strong>{pair.left_specification_key}</strong><span>{pair.right_specification_key}</span></div>
        <dl><div><dt>{t("model.scoreCorrelation")}</dt><dd>{value(pair.score_spearman)}</dd></div><div><dt>{t("model.spreadCorrelation")}</dt><dd>{value(pair.spread_correlation)}</dd></div><div><dt>{t("model.overlap")}</dt><dd>{percent(pair.mean_top2_overlap)}</dd></div></dl>
      </article>)}</div>}
    </section>
  </div>;
}
