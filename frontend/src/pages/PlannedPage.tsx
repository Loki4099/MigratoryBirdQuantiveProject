import { useTranslation } from "react-i18next";

export function PlannedPage({ titleKey, milestone }: { titleKey: string; milestone: string }) {
  const { t } = useTranslation();
  return (
    <div className="page planned-page">
      <p className="eyebrow">{milestone} / {t("common.planned")}</p>
      <h1>{t(titleKey)}</h1>
      <div className="planned-illustration"><div className="egg"><span /></div><i /></div>
      <h2>{t("planned.title")}</h2>
      <p>{t("planned.body", { milestone })}</p>
    </div>
  );
}
