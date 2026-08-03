import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function LoadingState() {
  const { t } = useTranslation();
  return <div className="state-card" role="status"><span className="loader" />{t("common.loading")}</div>;
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="state-card error" role="alert">
      <strong>{t("states.error")}</strong><span>{error.message}</span>
      {retry && <button className="secondary-button" onClick={retry}>{t("common.retry")}</button>}
    </div>
  );
}

export function EmptyState() {
  const { t } = useTranslation();
  return <div className="state-card empty">{t("common.noData")}</div>;
}

export function QualityBadge({ state, children }: { state: string; children?: ReactNode }) {
  const { t } = useTranslation();
  return <span className={`quality-badge quality-${state}`}><i />{children ?? t(`states.${state}`)}</span>;
}
