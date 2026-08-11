import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

const statuses = ["published", "tainted", "invalidated", "superseded", "retired", "draft"];

export function ArtifactsPage() {
  const { t, i18n } = useTranslation();
  const query = useQuery({ queryKey: ["artifacts", "all"], queryFn: () => api.artifacts(statuses) });
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />;

  return (
    <div className="page">
      <header className="page-heading"><div><p className="eyebrow">LINEAGE / ARTIFACTS</p><h1>{t("artifact.title")}</h1><p>{t("artifact.subtitle")}</p></div><QualityBadge state={query.data?.quality.state ?? "ok"} /></header>
      {!query.data?.items.length ? <EmptyState /> : (
        <div className="artifact-list">
          <div className="artifact-list-head"><span>{t("artifact.key")}</span><span>{t("artifact.type")}</span><span>{t("common.version")}</span><span>{t("common.status")}</span><span /></div>
          {query.data.items.map((item) => (
            <article className="artifact-row" key={item.artifact_id}>
              <div><strong>{item.artifact_key}</strong><code>{item.artifact_id.slice(0, 8)}</code></div>
              <span>{item.artifact_type}</span><span>v{item.version_number}</span>
              <QualityBadge state={item.quality.state}>{t(`states.${item.status}`)}</QualityBadge>
              <Link className="arrow-link" to={`/artifacts/${item.artifact_id}?lang=${i18n.resolvedLanguage}`}>{t("common.open")} →</Link>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
