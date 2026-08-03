import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

export function DashboardPage() {
  const { t } = useTranslation();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
  const artifacts = useQuery({ queryKey: ["artifacts", "published"], queryFn: () => api.artifacts() });

  if (health.isLoading || capabilities.isLoading || artifacts.isLoading) return <LoadingState />;
  const error = health.error ?? capabilities.error ?? artifacts.error;
  if (error) return <ErrorState error={error} retry={() => void health.refetch()} />;

  const available = capabilities.data?.domains.filter((domain) => domain.availability === "available").length ?? 0;
  return (
    <div className="page dashboard-page">
      <section className="hero">
        <p className="eyebrow">{t("dashboard.eyebrow")}</p>
        <h1>{t("dashboard.title")}</h1>
        <p>{t("dashboard.subtitle")}</p>
        <div className="hero-line" />
      </section>
      <section className="stat-grid">
        <article className="stat-card"><span>{t("dashboard.systemHealth")}</span><QualityBadge state="ok" /><strong>{health.data?.database_revision}</strong></article>
        <article className="stat-card"><span>{t("dashboard.publishedObjects")}</span><strong className="stat-number">{artifacts.data?.total ?? 0}</strong><small>{t("dashboard.traceHint")}</small></article>
        <article className="stat-card"><span>{t("dashboard.currentScope")}</span><strong className="stat-number">{available} / {capabilities.data?.domains.length ?? 0}</strong><small>{t("common.available")}</small></article>
        <article className="stat-card accent"><span>{t("dashboard.nextMilestone")}</span><strong>{t("dashboard.nextValue")}</strong><small>M4</small></article>
      </section>
      <section className="foundation-strip">
        <div><span>01</span><strong>Canonical identity</strong><small>canonical-json-v2</small></div>
        <div><span>02</span><strong>Immutable publication</strong><small>PostgreSQL enforced</small></div>
        <div><span>03</span><strong>Lineage manifest</strong><small>Complete dependency snapshot</small></div>
      </section>
    </div>
  );
}
