import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

export function AssetsPage() {
  const { t } = useTranslation();
  const assets = useQuery({ queryKey: ["catalog", "assets"], queryFn: api.assets });
  const requirements = useQuery({
    queryKey: ["catalog", "data-requirements"],
    queryFn: api.dataRequirements,
  });

  if (assets.isLoading || requirements.isLoading) return <LoadingState />;
  if (assets.error) return <ErrorState error={assets.error} retry={() => void assets.refetch()} />;
  if (requirements.error) {
    return <ErrorState error={requirements.error} retry={() => void requirements.refetch()} />;
  }
  if (!assets.data?.items.length) return <EmptyState />;

  const candidates = assets.data.items.filter((item) => item.universe_role === "candidate");
  const benchmark = assets.data.items.find((item) => item.universe_role === "benchmark");

  return (
    <div className="page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">CATALOG / RESEARCH SCOPE</p>
          <h1>{t("assets.title")}</h1>
          <p>{t("assets.subtitle")}</p>
        </div>
        <QualityBadge state={assets.data.quality.state} />
      </header>

      <section className="scope-strip">
        <div><span>{t("assets.universe")}</span><strong>{assets.data.universe_key}</strong></div>
        <div><span>{t("assets.asOf")}</span><strong>{assets.data.as_of_date}</strong></div>
        <div><span>{t("assets.candidates")}</span><strong>{candidates.length}</strong></div>
        <div><span>{t("assets.benchmark")}</span><strong>{benchmark?.symbol ?? "—"}</strong></div>
      </section>

      <section className="catalog-section">
        <div className="section-heading">
          <div><p className="eyebrow">TRADABLE ASSETS</p><h2>{t("assets.members")}</h2></div>
          <code>{assets.data.release_artifact_id.slice(0, 8)}</code>
        </div>
        <div className="asset-grid">
          {assets.data.items.map((item) => (
            <article className="asset-card" key={item.asset_id}>
              <div className="asset-symbol"><strong>{item.symbol}</strong><span>{item.universe_role}</span></div>
              <h3>{item.name}</h3>
              <dl>
                <div><dt>{t("assets.style")}</dt><dd>{item.classifications.style_exposure}</dd></div>
                <div><dt>{t("assets.listing")}</dt><dd>{item.venue_mic} · {item.currency}</dd></div>
                <div><dt>{t("assets.calendar")}</dt><dd>{item.calendar_key}</dd></div>
              </dl>
            </article>
          ))}
        </div>
      </section>

      <section className="catalog-section">
        <div className="section-heading">
          <div><p className="eyebrow">DATA CONTRACT</p><h2>{t("assets.requirements")}</h2></div>
          <code>v{requirements.data?.version_number}</code>
        </div>
        <div className="requirement-list">
          {requirements.data?.items.map((item) => (
            <article key={item.requirement_key}>
              <div><strong>{item.requirement_key}</strong><span>{item.subject}</span></div>
              <code>{item.series_key}</code>
              <p>{item.fields.join(" · ")}</p>
            </article>
          ))}
        </div>
        <p className="scope-note">{t("assets.rateNote")}</p>
      </section>
    </div>
  );
}
