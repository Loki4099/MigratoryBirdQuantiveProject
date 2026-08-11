import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

export function DataPage() {
  const { t } = useTranslation();
  const overview = useQuery({ queryKey: ["data", "overview"], queryFn: api.dataOverview });

  if (overview.isLoading) return <LoadingState />;
  if (overview.error) {
    return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  }
  if (!overview.data) return <EmptyState />;

  const { sources, datasets, bundle, eligibility, quality } = overview.data;
  const issueCount = datasets.reduce((total, dataset) => total + dataset.issues.length, 0);

  return (
    <div className="page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">DATA / PUBLISHED CHAIN</p>
          <h1>{t("data.title")}</h1>
          <p>{t("data.subtitle")}</p>
        </div>
        <QualityBadge state={quality.state} />
      </header>

      <section className="scope-strip data-scope-strip">
        <div><span>{t("data.sourceSnapshots")}</span><strong>{sources.length}</strong></div>
        <div><span>{t("data.datasets")}</span><strong>{datasets.length}</strong></div>
        <div><span>{t("data.issues")}</span><strong>{issueCount}</strong></div>
        <div><span>{t("data.eligible")}</span><strong>{eligibility ? `${eligibility.eligible_count}/${eligibility.member_count}` : "—"}</strong></div>
      </section>

      <section className="catalog-section">
        <div className="section-heading">
          <div><p className="eyebrow">CANONICAL & DERIVED</p><h2>{t("data.datasets")}</h2></div>
          <code>{t("data.publishedOnly")}</code>
        </div>
        {datasets.length === 0 ? <EmptyState /> : (
          <div className="dataset-list">
            {datasets.map((dataset) => (
              <article key={dataset.artifact_id}>
                <div className="dataset-title">
                  <div><strong>{dataset.dataset_key}</strong><span>{dataset.dataset_kind} · {dataset.value_kind}</span></div>
                  <QualityBadge state={dataset.quality.state} />
                </div>
                <div className="dataset-metrics">
                  <span>{t("common.version")} <strong>v{dataset.version_number}</strong></span>
                  <span>{t("data.coverage")} <strong>{dataset.coverage_start} → {dataset.coverage_end}</strong></span>
                  <span>{t("data.rows")} <strong>{dataset.row_count.toLocaleString()}</strong></span>
                </div>
                <div className="coverage-chips">
                  {dataset.coverage.map((item) => (
                    <span key={item.subject_key}>{item.subject_key} · {item.observation_count.toLocaleString()}</span>
                  ))}
                </div>
                {dataset.issues.map((issue, index) => (
                  <p className={`data-issue issue-${issue.severity}`} key={`${issue.rule_code}-${index}`}>
                    <strong>{issue.rule_code}</strong> {issue.message}
                  </p>
                ))}
              </article>
            ))}
          </div>
        )}
      </section>

      <div className="data-detail-grid">
        <section className="catalog-section">
          <div className="section-heading"><div><p className="eyebrow">LOCKED INPUT SET</p><h2>{t("data.bundle")}</h2></div></div>
          {bundle ? (
            <article className="data-panel">
              <strong>{bundle.name}</strong><code>{bundle.bundle_key} · v{bundle.version_number}</code>
              <p>{bundle.coverage_start} → {bundle.coverage_end}</p>
              <ul>{bundle.members.map((member) => <li key={member.role}><span>{member.role}</span><code>{member.artifact_key} · v{member.version_number}</code></li>)}</ul>
            </article>
          ) : <EmptyState />}
        </section>

        <section className="catalog-section">
          <div className="section-heading"><div><p className="eyebrow">UNIVERSE READINESS</p><h2>{t("data.eligibility")}</h2></div></div>
          {eligibility ? (
            <article className="data-panel">
              <strong>{eligibility.snapshot_key}</strong>
              <code>{t("data.warmup")} · {eligibility.warmup_observations}</code>
              <p>{eligibility.requested_start} → {eligibility.requested_end}</p>
              <ul>{eligibility.items.map((item) => <li key={item.asset_id}><span>{item.symbol}</span><QualityBadge state={item.is_eligible ? "ok" : "warning"}>{item.is_eligible ? t("data.ready") : t("data.blocked")}</QualityBadge></li>)}</ul>
            </article>
          ) : <EmptyState />}
        </section>
      </div>

      <section className="catalog-section">
        <div className="section-heading"><div><p className="eyebrow">RAW EVIDENCE</p><h2>{t("data.sourceSnapshots")}</h2></div></div>
        {sources.length === 0 ? <EmptyState /> : <div className="source-list">{sources.map((source) => (
          <article key={source.artifact_id}><div><strong>{source.series_key}</strong><span>{source.provider_key}</span></div><code>{source.snapshot_key}</code><span>{new Date(source.fetched_at).toLocaleString()}</span></article>
        ))}</div>}
      </section>
    </div>
  );
}
