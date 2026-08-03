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
      <pre className="endpoint-list"><code>GET /api/v2/health{"\n"}GET /api/v2/capabilities{"\n"}GET /api/v2/artifacts{"\n"}GET /api/v2/artifacts/&#123;artifact_id&#125;/lineage</code></pre>
    </div>
  );
}
