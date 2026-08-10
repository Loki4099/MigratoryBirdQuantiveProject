import { NavLink, Outlet } from "react-router-dom";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { setLanguage, type SupportedLanguage } from "../i18n";
import fledglingLogo from "../assets/fledgling-logo.svg";
import { useWorkspaceSelection } from "../workspace/WorkspaceSelectionContext";

const navigation = [
  { label: "nav.overview", items: [["nav.workspace", "/"]] },
  {
    label: "nav.research",
    items: [
      ["nav.assets", "/assets"],
      ["nav.factors", "/factors"], ["nav.signals", "/signals"],
      ["nav.models", "/models"],
    ],
  },
  {
    label: "nav.products",
    items: [
      ["nav.strategies", "/strategies"], ["nav.experiments", "/experiments"],
      ["nav.products", "/products"],
    ],
  },
  {
    label: "nav.system",
    items: [["nav.artifacts", "/artifacts"], ["nav.runs", "/runs"], ["nav.api", "/api"]],
  },
] as const;

export function AppShell() {
  const { t, i18n } = useTranslation();
  const workspace = useWorkspaceSelection();
  const language = (i18n.resolvedLanguage ?? "zh-CN") as SupportedLanguage;
  const navigationSearch = `?lang=${language}&frequency=${workspace.frequency}`;
  const [navigationOpen, setNavigationOpen] = useState(false);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">{t("common.skipToContent")}</a>
      <aside className={`sidebar${navigationOpen ? " nav-open" : ""}`}>
        <div className="brand-lockup">
          <img className="brand-mark" src={fledglingLogo} alt="" aria-hidden="true" />
          <div><strong>{t("brand.name")}</strong><small>{t("brand.stage")}</small></div>
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
          <span className="topbar-context">MIGRATORY BIRD LAB · v0.21</span>
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
