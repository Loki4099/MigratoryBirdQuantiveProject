import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { ErrorState, LoadingState } from "../components/QueryState";
import { FormulaDisplay } from "../components/ResearchText";
import { useWorkspaceSelection } from "../workspace/WorkspaceSelectionContext";

type FactorOption = Awaited<ReturnType<typeof api.workspaceOptions>>["factor_families"][number];

export function FactorsPage() {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const workspace = useWorkspaceSelection();
  const options = useQuery({
    queryKey: ["workspace", "options", workspace.frequency, workspace.assetSecurityIds, workspace.assetDataInputs, workspace.factorVariantKeys, workspace.signalVersionKeys],
    queryFn: () => api.workspaceOptions({ frequency: workspace.frequency, assetSecurityIds: workspace.assetSecurityIds, assetDataInputs: workspace.assetDataInputs, factorVariantKeys: workspace.factorVariantKeys, signalVersionKeys: workspace.signalVersionKeys }),
    placeholderData: (previous) => previous,
  });
  if (options.isLoading) return <LoadingState />;
  if (options.error) return <ErrorState error={options.error} retry={() => void options.refetch()} />;
  return <div className="page">
    <header className="page-heading"><div><p className="eyebrow">WORKSPACE / FACTOR CATALOG</p><h1>{chinese ? "因子库与参数选择" : "Factor catalog and parameter choices"}</h1><p>{chinese ? "每个因子家族只显示一张信息卡；公式、输入、时间语义与全部固定参数都在卡片内完成查看和选择。v0.2 的诊断实例清单已移除。" : "Each Factor family appears once with formula, inputs, time semantics, and every fixed parameter choice. The legacy diagnostic instance list has been removed."}</p></div></header>
    {options.data && <FactorSelectionCatalog families={options.data.factor_families} selected={workspace.factorVariantKeys} toggle={workspace.toggleFactor} chinese={chinese} />}
  </div>;
}

function FactorSelectionCatalog({ families, selected, toggle, chinese }: { families: FactorOption[]; selected: string[]; toggle: (key: string) => void; chinese: boolean }) {
  const [search, setSearch] = useState("");
  const term = useDeferredValue(search).trim().toLowerCase();
  const visible = families.filter((family) => !term || [family.key, family.family, family.formula, ...family.inputs].join(" ").toLowerCase().includes(term));
  return <section className="catalog-section workspace-option-section">
    <div className="section-heading"><div><p className="eyebrow">FACTOR FAMILIES</p><h2>{chinese ? "因子家族与参数选择" : "Factor families and parameter choices"}</h2></div><label className="compact-search"><span>{chinese ? "搜索" : "Search"}</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={chinese ? "收益、收盘价、流动性……" : "return, close, liquidity…"} /></label></div>
    <p className="scope-note">{chinese ? "同一家族的固定参数可以单选或多选，但不能手动输入。原始 OHLCV 也作为原始因子家族出现。" : "Fixed variants within one family may be selected singly or together, but never typed manually. Raw OHLCV fields also appear as Factor families."}</p>
    <div className="workspace-family-grid">{visible.map((family) => <article className="workspace-family-card research-family-card" key={family.key}>
      <header><div><span>{family.raw ? (chinese ? "原始因子" : "RAW FACTOR") : family.family}</span><h3>{family.key}</h3></div><code>{family.variants.length} {chinese ? "个参数版本" : "variants"}</code></header>
      <FormulaDisplay factorKey={family.key} formula={family.formula} />
      <dl className="research-card-facts"><div><dt>{chinese ? "输入字段" : "Inputs"}</dt><dd>{family.inputs.join(" · ") || "—"}</dd></div><div><dt>{chinese ? "输出单位" : "Output unit"}</dt><dd>{family.output_unit}</dd></div><div><dt>{chinese ? "时间语义" : "Time semantics"}</dt><dd>{family.time_semantics}</dd></div><div><dt>{chinese ? "计算实现" : "Implementation"}</dt><dd>{family.implementation_key}</dd></div></dl>
      <div className="workspace-variant-choices">{family.variants.map((variant) => {
        const isSelected = selected.includes(variant.key);
        return <label className={variant.selectable === false ? "disabled" : ""} key={variant.key} title={(variant.reason_codes ?? []).join(", ")}><input type="checkbox" checked={isSelected} disabled={variant.selectable === false && !isSelected} onChange={() => toggle(variant.key)} /><span><strong>{Object.entries(variant.parameters).map(([key, value]) => `${key}=${String(value)}`).join(" · ") || (chinese ? "无额外参数" : "No extra parameters")}</strong><code>{variant.key}</code>{variant.selectable === false && <small>{(variant.reason_codes ?? []).join(" · ")}</small>}</span></label>;
      })}</div>
    </article>)}</div>
  </section>;
}
