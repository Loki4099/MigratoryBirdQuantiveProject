import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { ResearchKey, researchLabel, researchRationale } from "../components/ResearchText";

type Frequency = "weekly" | "monthly";

function value(input: number | null, digits = 3) {
  if (input === null) return "—";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(input);
}

function percent(input: number | null, digits = 1) {
  if (input === null) return "—";
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    maximumFractionDigits: digits,
  }).format(input);
}

export function SignalsPage() {
  const { t, i18n } = useTranslation();
  const [search, setSearch] = useSearchParams();
  const frequency: Frequency = search.get("frequency") === "monthly" ? "monthly" : "weekly";
  const overview = useQuery({
    queryKey: ["signals", "overview", frequency],
    queryFn: () => api.signalOverview(frequency),
  });

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

  return (
    <div className="page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">SIGNAL / DIRECTIONAL DIAGNOSTICS</p>
          <h1>{t("signal.title")}</h1>
          <p>{t("signal.subtitle")}</p>
        </div>
        <QualityBadge state={data.quality.state} />
      </header>

      <div className="signal-frequency" aria-label={t("signal.frequency")}>
        {(["weekly", "monthly"] as const).map((item) => (
          <button
            className={frequency === item ? "active" : ""}
            key={item}
            onClick={() => selectFrequency(item)}
            type="button"
          >
            {t(`signal.${item}`)}
          </button>
        ))}
      </div>

      <section className="scope-strip signal-scope-strip">
        <div><span>{t("signal.context")}</span><strong>{data.coverage_start} → {data.coverage_end}</strong></div>
        <div><span>{t("signal.target")}</span><strong>{researchLabel(data.target_key, i18n.resolvedLanguage)}</strong></div>
        <div><span>{t("signal.periods")}</span><strong>{data.common_period_count}</strong></div>
        <div><span>{t("signal.signals")}</span><strong>{data.signal_count}</strong></div>
        <div><span>{t("signal.highPairs")}</span><strong>{highPairs.length} / {data.pair_count}</strong></div>
      </section>
      <p className="scope-note">{t("signal.sampleNote")}</p>

      <section className="catalog-section">
        <div className="section-heading">
          <div><p className="eyebrow">IC / SPREAD / BEHAVIOR</p><h2>{t("signal.library")}</h2></div>
          <Link className="arrow-link" to={`/artifacts/${data.evaluation_artifact_id}`}>{t("signal.lineage")} →</Link>
        </div>
        <div className="signal-grid">
          {data.signals.map((signal) => (
            <article className="signal-card" key={signal.signal_dataset_artifact_id}>
              <div className="signal-card-heading">
                <div><span><ResearchKey value={signal.economic_family} /> · <ResearchKey value={signal.research_tier} /></span><strong>{signal.signal_key}</strong></div>
                <QualityBadge state={signal.quality.state} />
              </div>
              <p>{researchRationale(signal.rationale, i18n.resolvedLanguage)}</p>
              <div className="signal-tags">
                <code>{t("signal.direction")}: <ResearchKey value={signal.direction} /></code>
                <code>{t("signal.output")}: <ResearchKey value={signal.output_type} /></code>
                <code>{signal.factor_variant_key}</code>
              </div>
              <div className="signal-stat-grid">
                <span>{t("signal.rankIc")}<strong>{value(signal.full.mean_rank_ic)}</strong></span>
                <span>{t("signal.positiveIc")}<strong>{percent(signal.full.positive_ic_ratio)}</strong></span>
                <span>{t("signal.topBottom")}<strong>{percent(signal.full.mean_top_bottom_spread, 2)}</strong></span>
                <span>{t("signal.informationRatio")}<strong>{value(signal.full.information_ratio)}</strong></span>
                <span>{t("signal.activity")}<strong>{percent(signal.full.event_rate ?? signal.full.non_neutral_rate)}</strong></span>
                <span>{t("signal.turnover")}<strong>{percent(signal.full.mean_top2_turnover)}</strong></span>
              </div>
              <details className="signal-stability">
                <summary>{t("signal.stability")} · {signal.stability.length}</summary>
                {signal.stability.length === 0 ? <p>{t("signal.noStability")}</p> : signal.stability.map((window) => (
                  <div key={window.window_key}>
                    <code>{window.window_key.replace("year:", "")}</code>
                    <span>IC {value(window.mean_rank_ic)}</span>
                    <span>+IC {percent(window.positive_ic_ratio)}</span>
                    <span>T−B {percent(window.mean_top_bottom_spread, 2)}</span>
                    <small>n={window.period_count}</small>
                  </div>
                ))}
              </details>
            </article>
          ))}
        </div>
      </section>

      <section className="catalog-section">
        <div className="section-heading"><div><p className="eyebrow">|ρ| ≥ {data.high_correlation_threshold}</p><h2>{t("signal.redundancy")}</h2></div></div>
        <p className="scope-note">{t("signal.redundancyNote")}</p>
        {highPairs.length === 0 ? <p className="factor-empty-note">{t("signal.noHighPairs")}</p> : (
          <div className="signal-pair-list">{highPairs.slice(0, 30).map((pair) => (
            <article key={`${pair.left_signal_key}-${pair.right_signal_key}`}>
              <div><strong>{pair.left_signal_key}</strong><span>{pair.right_signal_key}</span></div>
              <dl>
                <div><dt>{t("signal.scoreCorrelation")}</dt><dd>{value(pair.score_spearman)}</dd></div>
                <div><dt>{t("signal.spreadCorrelation")}</dt><dd>{value(pair.spread_correlation)}</dd></div>
                <div><dt>{t("signal.overlap")}</dt><dd>{percent(pair.mean_top2_overlap)}</dd></div>
              </dl>
            </article>
          ))}</div>
        )}
      </section>

      {data.issues.length > 0 && <section className="catalog-section">
        <div className="section-heading"><div><p className="eyebrow">QUALITY / SAMPLE</p><h2>{t("signal.issues")}</h2></div></div>
        <div className="factor-issue-list">{data.issues.map((issue, index) => (
          <p key={`${issue.signal_key}-${issue.issue_code}-${index}`}><strong>{issue.signal_key} · {issue.issue_code}</strong>{issue.message}</p>
        ))}</div>
      </section>}
    </div>
  );
}
