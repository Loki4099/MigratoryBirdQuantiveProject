import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { setLanguage, type SupportedLanguage } from "../i18n";
import fledglingLogo from "../assets/fledgling-logo.svg";
import { useV022ReleaseControl } from "../release/useV022ReleaseControl";

function v022Navigation(prefix: "" | "/workspace-v022") {
  return [
    {
      label: "nav.research",
      items: [
        ["nav.assets", prefix ? `${prefix}/context` : "/research-context"],
        ["nav.processing1", `${prefix}/processing-1`],
        ["nav.processing2", `${prefix}/processing-2`],
        ["nav.processing3", `${prefix}/processing-3`],
        ["nav.aggregation", `${prefix}/aggregation`],
      ],
    },
    {
      label: "nav.products",
      items: [
        ["nav.strategies", prefix ? `${prefix}/strategy` : "/strategy-configuration"],
        ["nav.experiments", "/experiments"],
        ["nav.products", "/products"],
      ],
    },
    {
      label: "nav.system",
      items: [["nav.artifacts", "/artifacts"], ["nav.runs", "/runs"], ["nav.api", "/api"]],
    },
  ] as const;
}

const neutralNavigation = [
  {
    label: "nav.system",
    items: [["nav.artifacts", "/artifacts"], ["nav.runs", "/runs"], ["nav.api", "/api"]],
  },
] as const;

function buildNavigationSearch(
  locationSearch: string,
  language: SupportedLanguage,
  frequency: "weekly" | "monthly",
) {
  const current = new URLSearchParams(locationSearch);
  const params = new URLSearchParams({
    lang: language,
    frequency,
    contract: "v0.22",
  });
  const launchBatchId = current.get("launch_batch");
  const graphSuiteId = current.get("graph_suite");
  if (launchBatchId) params.set("launch_batch", launchBatchId);
  else if (graphSuiteId) params.set("graph_suite", graphSuiteId);
  return `?${params.toString()}`;
}

export function AppShell() {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const release = useV022ReleaseControl();
  const previewMode = location.pathname === "/workspace-v022" || location.pathname.startsWith("/workspace-v022/");
  const neutralMode = release.isLoading || release.isError || release.data?.maintenance_read_only === true;
  const language = (i18n.resolvedLanguage ?? "zh-CN") as SupportedLanguage;
  const requestedFrequency = new URLSearchParams(location.search).get("frequency");
  const frequency = requestedFrequency === "monthly" ? "monthly" : "weekly";
  const navigationSearch = buildNavigationSearch(location.search, language, frequency);
  const navigation = neutralMode
    ? neutralNavigation
    : v022Navigation(previewMode ? "/workspace-v022" : "");
  const [navigationOpen, setNavigationOpen] = useState(false);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">{t("common.skipToContent")}</a>
      <aside className={`sidebar${navigationOpen ? " nav-open" : ""}`}>
        <div className="brand-lockup">
          <img className="brand-mark" src={fledglingLogo} alt="" aria-hidden="true" />
          <div><strong>{t("brand.name")}</strong><small>{language === "zh-CN" ? "研究与产品工作台 · v0.22" : "Research and product workspace · v0.22"}</small></div>
        </div>
        <button
          className="mobile-nav-toggle"
          type="button"
          aria-expanded={navigationOpen}
          aria-controls="primary-navigation"
          onClick={() => setNavigationOpen((open) => !open)}
        >{navigationOpen ? (language === "zh-CN" ? "关闭导航" : "Close menu") : (language === "zh-CN" ? "打开导航" : "Open menu")}</button>
        <nav id="primary-navigation" aria-label={t("common.primaryNavigation")}>
          {navigation.map((group) => (
            <section className="nav-group" key={group.label}>
              <p>{t(group.label)}</p>
              {group.items.map(([label, path]) => (
                <NavLink key={path} to={{ pathname: path, search: navigationSearch }} end={path === "/"} onClick={() => setNavigationOpen(false)}>
                  {t(label)}
                </NavLink>
              ))}
            </section>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="read-only-dot" /> {t("common.readOnly")}
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span className="topbar-context">MIGRATORY BIRD LAB · v0.22</span>
          <div className="language-switch" aria-label={t("common.language")}>
            {(["zh-CN", "en"] as const).map((item) => (
              <button
                className={language === item ? "active" : ""}
                key={item}
                onClick={() => void setLanguage(item)}
                aria-pressed={language === item}
                type="button"
              >
                {item === "zh-CN" ? "中文" : "EN"}
              </button>
            ))}
          </div>
        </header>
        <main id="main-content" tabIndex={-1}><Outlet /></main>
      </div>
    </div>
  );
}
