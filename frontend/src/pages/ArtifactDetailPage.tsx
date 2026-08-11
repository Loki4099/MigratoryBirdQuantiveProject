import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

function HashValue({ value }: { value?: string | null }) {
  return <code className="hash-value">{value ?? "—"}</code>;
}

export function ArtifactDetailPage() {
  const { artifactId = "" } = useParams();
  const { t, i18n } = useTranslation();
  const detail = useQuery({ queryKey: ["artifact", artifactId], queryFn: () => api.artifact(artifactId), enabled: Boolean(artifactId) });
  const lineage = useQuery({ queryKey: ["lineage", artifactId], queryFn: () => api.lineage(artifactId), enabled: Boolean(detail.data?.lineage_url) });
  if (detail.isLoading) return <LoadingState />;
  if (detail.error) return <ErrorState error={detail.error} retry={() => void detail.refetch()} />;
  const artifact = detail.data?.artifact;
  if (!artifact) return null;

  return (
    <div className="page">
      <Link className="back-link" to={`/artifacts?lang=${i18n.resolvedLanguage}`}>← {t("common.back")}</Link>
      <header className="page-heading detail-heading"><div><p className="eyebrow">{artifact.artifact_type}</p><h1>{artifact.artifact_key}</h1><p>{artifact.artifact_id}</p></div><QualityBadge state={artifact.quality.state}>{t(`states.${artifact.status}`)}</QualityBadge></header>
      <section className="detail-grid">
        <article className="detail-card"><span>{t("artifact.fingerprint")}</span><HashValue value={artifact.semantic_fingerprint} /></article>
        <article className="detail-card"><span>{t("artifact.contentHash")}</span><HashValue value={artifact.content_hash} /></article>
        <article className="detail-card"><span>{t("artifact.manifest")}</span>{lineage.isLoading ? <small>{t("common.loading")}</small> : <HashValue value={lineage.data?.manifest_hash} />}</article>
        <article className="detail-card"><span>{t("artifact.canonical")}</span><strong>{lineage.data?.canonical_version ?? "—"}</strong></article>
      </section>
      <section className="dependency-grid">
        <article><h2>{t("common.dependencies")}</h2><strong>{detail.data?.direct_dependencies.length ?? 0}</strong></article>
        <article><h2>{t("common.dependents")}</h2><strong>{detail.data?.direct_dependents.length ?? 0}</strong></article>
        <article><h2>{t("artifact.lineage")}</h2><strong>{lineage.data?.artifacts.length ?? 0}</strong><small> artifacts</small></article>
      </section>
    </div>
  );
}
