import { useTranslation } from "react-i18next";

export function ApiPage() {
  const { t } = useTranslation();
  return (
    <div className="page api-page">
      <header className="page-heading"><div><p className="eyebrow">SYSTEM / CONTRACT</p><h1>{t("api.title")}</h1><p>{t("api.subtitle")}</p></div></header>
      <section className="api-actions">
        <a className="primary-button" href="/api/v2/docs" target="_blank">{t("api.docs")} ↗</a>
        <a className="secondary-button" href="/api/v2/openapi.json" download>{t("api.contract")}</a>
      </section>
      <section className="rule-card"><span>GET</span><div><h2>{t("api.ruleTitle")}</h2><p>{t("api.ruleBody")}</p></div></section>
      <pre className="endpoint-list"><code>GET /api/v2/health{"\n"}GET /api/v2/capabilities{"\n"}GET /api/v2/release-control{"\n"}GET /api/v2/catalog/assets{"\n"}GET /api/v2/data/overview{"\n"}GET /api/v2/factors/overview{"\n"}GET /api/v2/signals/overview{"\n"}GET /api/v2/models/overview{"\n"}GET /api/v2/strategies/overview{"\n"}GET /api/v2/experiments/overview{"\n"}GET /api/v2/experiments/results/&#123;artifact_id&#125;{"\n"}GET /api/v2/experiments/results/&#123;artifact_id&#125;/decisions{"\n"}GET /api/v2/rankings/products{"\n"}GET /api/v2/compare/products{"\n"}GET /api/v2/artifacts{"\n"}GET /api/v2/artifacts/&#123;artifact_id&#125;/lineage</code></pre>
    </div>
  );
}
