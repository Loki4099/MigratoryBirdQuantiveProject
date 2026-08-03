import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

function number(value: number | null, digits = 4) {
  if (value === null) return "—";
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: digits }).format(value);
}

function distributionStyle(minimum: number, p05: number, p95: number, maximum: number) {
  const span = maximum - minimum;
  if (span <= 0) return { left: "0%", width: "100%" };
  const left = ((p05 - minimum) / span) * 100;
  const width = Math.max(1, ((p95 - p05) / span) * 100);
  return { left: `${left}%`, width: `${width}%` };
}

export function FactorsPage() {
  const { t } = useTranslation();
  const overview = useQuery({ queryKey: ["factors", "overview"], queryFn: api.factorOverview });

  if (overview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (!overview.data) return <EmptyState />;

  const data = overview.data;
  const highCorrelations = data.correlations.filter((item) => item.high_correlation);
  const parameterStability = data.correlations.filter((item) => item.same_definition);

  return (
    <div className="page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">FACTOR / MEASUREMENT DIAGNOSTICS</p>
          <h1>{t("factor.title")}</h1>
          <p>{t("factor.subtitle")}</p>
        </div>
        <QualityBadge state={data.quality.state} />
      </header>

      <section className="scope-strip factor-scope-strip">
        <div><span>{t("factor.context")}</span><strong>{data.coverage_start} → {data.coverage_end}</strong></div>
        <div><span>{t("factor.variants")}</span><strong>{data.dataset_count}</strong></div>
        <div><span>{t("factor.assets")}</span><strong>{data.asset_count}</strong></div>
        <div><span>{t("factor.highPairs")}</span><strong>{highCorrelations.length} / {data.pair_count}</strong></div>
      </section>

      <section className="catalog-section">
        <div className="section-heading">
          <div><p className="eyebrow">DISTRIBUTION / COVERAGE</p><h2>{t("factor.library")}</h2></div>
          <Link className="arrow-link" to={`/artifacts/${data.diagnostic_artifact_id}`}>{t("factor.lineage")} →</Link>
        </div>
        <div className="factor-grid">
          {data.datasets.map((dataset) => (
            <article className="factor-card" key={dataset.factor_dataset_artifact_id}>
              <div className="factor-card-title">
                <div><span>{dataset.measurement_family}</span><strong>{dataset.variant_key}</strong></div>
                <QualityBadge state={dataset.quality.state} />
              </div>
              <p>{dataset.formula}</p>
              <div className="distribution-track" aria-label={`${dataset.variant_key} p05 p95`}>
                <i style={distributionStyle(dataset.minimum, dataset.p05, dataset.p95, dataset.maximum)} />
                <b style={{ left: `${dataset.maximum === dataset.minimum ? 50 : ((dataset.median - dataset.minimum) / (dataset.maximum - dataset.minimum)) * 100}%` }} />
              </div>
              <div className="factor-stat-grid">
                <span>{t("factor.mean")}<strong>{number(dataset.mean)}</strong></span>
                <span>{t("factor.median")}<strong>{number(dataset.median)}</strong></span>
                <span>{t("factor.std")}<strong>{number(dataset.standard_deviation)}</strong></span>
                <span>{t("factor.rows")}<strong>{dataset.observation_count.toLocaleString()}</strong></span>
              </div>
              <div className="factor-parameters">
                {Object.entries(dataset.parameters).map(([key, value]) => <code key={key}>{key}={String(value)}</code>)}
                <code>{dataset.preset_type}</code>
              </div>
            </article>
          ))}
        </div>
      </section>

      <div className="factor-diagnostic-grid">
        <section className="catalog-section">
          <div className="section-heading"><div><p className="eyebrow">SAME DEFINITION</p><h2>{t("factor.parameterStability")}</h2></div></div>
          <p className="scope-note">{t("factor.stabilityNote")}</p>
          <CorrelationList items={parameterStability} emptyLabel={t("factor.noPairs")} />
        </section>
        <section className="catalog-section">
          <div className="section-heading"><div><p className="eyebrow">|ρ| ≥ {data.high_correlation_threshold}</p><h2>{t("factor.redundancy")}</h2></div></div>
          <p className="scope-note">{t("factor.redundancyNote")}</p>
          <CorrelationList items={highCorrelations} emptyLabel={t("factor.noHighPairs")} />
        </section>
      </div>

      {data.issues.length > 0 && <section className="catalog-section">
        <div className="section-heading"><div><p className="eyebrow">QUALITY</p><h2>{t("factor.issues")}</h2></div></div>
        <div className="factor-issue-list">{data.issues.map((issue, index) => <p key={`${issue.variant_key}-${issue.issue_code}-${index}`}><strong>{issue.variant_key} · {issue.issue_code}</strong>{issue.message}</p>)}</div>
      </section>}
    </div>
  );
}

type Correlation = Awaited<ReturnType<typeof api.factorOverview>>["correlations"][number];

function CorrelationList({ items, emptyLabel }: { items: Correlation[]; emptyLabel: string }) {
  if (items.length === 0) return <p className="factor-empty-note">{emptyLabel}</p>;
  return <div className="correlation-list">{items.slice(0, 24).map((item) => (
    <article key={`${item.left_variant_key}-${item.right_variant_key}`}>
      <div><strong>{item.left_variant_key}</strong><span>{item.right_variant_key}</span></div>
      <code className={item.high_correlation ? "correlation-high" : ""}>ρ {number(item.spearman_correlation, 3)}</code>
    </article>
  ))}</div>;
}
