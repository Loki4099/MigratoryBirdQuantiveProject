import { NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { setLanguage, type SupportedLanguage } from "../i18n";

const navigation = [
  { label: "nav.overview", items: [["nav.dashboard", "/"]] },
  {
    label: "nav.research",
    items: [
      ["nav.assets", "/assets"], ["nav.data", "/data"],
      ["nav.factors", "/factors"], ["nav.signals", "/signals"],
      ["nav.models", "/models"],
    ],
  },
  {
    label: "nav.products",
    items: [
      ["nav.strategies", "/strategies"], ["nav.experiments", "/experiments"],
      ["nav.compare", "/compare"],
    ],
  },
  {
    label: "nav.system",
    items: [["nav.artifacts", "/artifacts"], ["nav.runs", "/runs"], ["nav.api", "/api"]],
  },
] as const;

export function AppShell() {
  const { t, i18n } = useTranslation();
  const language = (i18n.resolvedLanguage ?? "zh-CN") as SupportedLanguage;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="fledgling-mark" aria-hidden="true"><span /></div>
          <div><strong>{t("brand.name")}</strong><small>{t("brand.stage")}</small></div>
        </div>
        <nav aria-label="Primary navigation">
          {navigation.map((group) => (
            <section className="nav-group" key={group.label}>
              <p>{t(group.label)}</p>
              {group.items.map(([label, path]) => (
                <NavLink key={path} to={{ pathname: path, search: `?lang=${language}` }} end={path === "/"}>
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
          <span className="topbar-context">US STYLE ROTATION · v0.2</span>
          <div className="language-switch" aria-label="Language">
            {(["zh-CN", "en"] as const).map((item) => (
              <button
                className={language === item ? "active" : ""}
                key={item}
                onClick={() => void setLanguage(item)}
                type="button"
              >
                {item === "zh-CN" ? "中文" : "EN"}
              </button>
            ))}
          </div>
        </header>
        <main><Outlet /></main>
      </div>
    </div>
  );
}
