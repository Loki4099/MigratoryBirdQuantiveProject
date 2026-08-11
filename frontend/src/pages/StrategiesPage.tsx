import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { ErrorState, LoadingState } from "../components/QueryState";
import { catalogText, contractLabel } from "../researchCatalogText";
import { useWorkspaceSelection } from "../workspace/WorkspaceSelectionContext";

type StrategyOption = Awaited<ReturnType<typeof api.workspaceOptions>>["strategy_families"][number];

export function StrategiesPage() {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const workspace = useWorkspaceSelection();
  const options = useQuery({
    queryKey: ["workspace", "options", workspace.frequency, workspace.factorVariantKeys, workspace.signalVersionKeys, workspace.modelPresetKeys, workspace.strategyPresetKeys, workspace.assetSecurityIds, workspace.assetDataInputs],
    queryFn: () => api.workspaceOptions({ frequency: workspace.frequency, factorVariantKeys: workspace.factorVariantKeys, signalVersionKeys: workspace.signalVersionKeys, modelPresetKeys: workspace.modelPresetKeys, strategyPresetKeys: workspace.strategyPresetKeys, assetSecurityIds: workspace.assetSecurityIds, assetDataInputs: workspace.assetDataInputs }),
    placeholderData: (previous) => previous,
  });
  if (options.isLoading) return <LoadingState />;
  if (options.error) return <ErrorState error={options.error} retry={() => void options.refetch()} />;
  return <div className="page strategy-page">
    <header className="page-heading"><div><p className="eyebrow">WORKSPACE / STRATEGY CATALOG</p><h1>{chinese ? "策略规则与固定参数" : "Strategy rules and fixed parameters"}</h1><p>{chinese ? "策略只解释如何使用单个模型输出形成持仓。历史目标路径、v0.2 产品清单和收益诊断已从本层移除；完整结果只在实验层查看。" : "Strategies only define how one Model output becomes holdings. Legacy product lists, target paths, and return diagnostics are removed; complete results live in Experiments."}</p></div></header>
    {options.data && <StrategySelectionCatalog families={options.data.strategy_families ?? []} selected={workspace.strategyPresetKeys} toggle={workspace.toggleStrategy} usableAssets={options.data.usable_asset_count ?? 0} chinese={chinese} />}
  </div>;
}

function StrategySelectionCatalog({ families, selected, toggle, usableAssets, chinese }: { families: StrategyOption[]; selected: string[]; toggle: (key: string) => void; usableAssets: number; chinese: boolean }) {
  const [query, setQuery] = useState("");
  const term = useDeferredValue(query).trim().toLowerCase();
  return <section className="catalog-section workspace-option-section">
    <div className="section-heading"><div><p className="eyebrow">TOP-K STRATEGY FAMILIES</p><h2>{chinese ? "策略家族与参数组合" : "Strategy families and parameter combinations"}</h2></div><label className="compact-search"><span>{chinese ? "搜索" : "Search"}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={chinese ? "ETF、个股、K20、防御……" : "ETF, stock, K20, defense…"} /></label></div>
    <p className="scope-note">{chinese ? `当前已选且有数据的资产：${usableAssets}。每个模型与每个策略预设分别形成一条一对一回测分支。` : `${usableAssets} selected assets currently have usable data. Every Model × Strategy preset forms a separate one-to-one backtest branch.`}</p>
    <div className="workspace-family-grid strategy-family-grid">{families.map((family) => {
      const text = catalogText("strategy", family.key, chinese, { name: family.name, description: family.description });
      const presets = family.presets.filter((preset) => !term || `${family.key} ${text.name} ${preset.preset_key} ${Object.values(preset.parameters).join(" ")}`.toLowerCase().includes(term));
      if (term && presets.length === 0) return null;
      return <article className="workspace-family-card research-family-card strategy-contract-card" key={family.key}>
        <header><div><span>{contractLabel(family.required_instrument_type, chinese)} · {contractLabel(family.implementation_status, chinese)}</span><h3>{text.name}</h3><code>{family.key}</code></div></header>
        <p>{text.description}</p>
        <dl className="research-card-facts">
          <div><dt>{chinese ? "启动门槛" : "Launch minimum"}</dt><dd>{family.minimum_eligible_assets}</dd></div>
          <div><dt>{chinese ? "正式门槛" : "Formal minimum"}</dt><dd>{family.formal_minimum_eligible_assets}</dd></div>
          <div><dt>{chinese ? "当期分数覆盖" : "Score coverage"}</dt><dd>≥ {Math.round(family.coverage_ratio * 100)}%</dd></div>
          <div><dt>{chinese ? "调仓频率" : "Frequencies"}</dt><dd>{(family.supported_frequencies ?? []).map((item) => contractLabel(item, chinese)).join(" / ")}</dd></div>
          <div><dt>{chinese ? "主基准" : "Primary benchmark"}</dt><dd>{contractLabel(family.primary_benchmark, chinese)}</dd></div>
          <div><dt>{chinese ? "研究基准" : "Research benchmark"}</dt><dd>{contractLabel(family.research_benchmark, chinese)}</dd></div>
        </dl>
        <div className="strategy-parameter-guide"><strong>{chinese ? "可选参数范围" : "Published parameter choices"}</strong>{Object.entries(family.parameter_options ?? {}).map(([key, values]) => <p key={key}><span>{key}</span>{values.map((value) => <code key={String(value)}>{contractLabel(String(value), chinese)}</code>)}</p>)}</div>
        <div className="workspace-strategy-presets">{presets.map((preset) => {
          const isSelected = selected.includes(preset.preset_key);
          return <label className={!preset.selectable ? "disabled" : ""} key={preset.preset_key} title={preset.reason_codes.join(", ")}>
            <input type="checkbox" checked={isSelected} disabled={!preset.selectable && !isSelected} onChange={() => toggle(preset.preset_key)} />
            <span><strong>K={String(preset.parameters.target_k)} · {contractLabel(String(preset.parameters.defense), chinese)}</strong><small>{chinese ? "选股缓冲" : "buffer"}: {contractLabel(String(preset.parameters.selection_buffer), chinese)} · {chinese ? "行业上限" : "sector cap"}: {contractLabel(String(preset.parameters.sector_cap), chinese)} · {contractLabel(preset.research_mode, chinese)}</small><code>{preset.preset_key}</code>{!preset.selectable && <small>{preset.reason_codes.map((item) => contractLabel(item, chinese)).join(" · ")}</small>}</span>
          </label>;
        })}</div>
      </article>;
    })}</div>
  </section>;
}
