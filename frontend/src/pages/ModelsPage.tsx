import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { ErrorState, LoadingState } from "../components/QueryState";
import { catalogText, contractLabel } from "../researchCatalogText";
import { useWorkspaceSelection } from "../workspace/WorkspaceSelectionContext";

type Frequency = "weekly" | "monthly";
type ModelOption = Awaited<ReturnType<typeof api.workspaceOptions>>["model_families"][number];

export function ModelsPage() {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const [search, setSearch] = useSearchParams();
  const workspace = useWorkspaceSelection();
  const frequency: Frequency = workspace.frequency;
  const serializedSearch = search.toString();
  useEffect(() => {
    const updated = new URLSearchParams(serializedSearch);
    if (updated.get("frequency") === workspace.frequency) return;
    updated.set("frequency", workspace.frequency);
    setSearch(updated, { replace: true });
  }, [serializedSearch, setSearch, workspace.frequency]);
  const options = useQuery({
    queryKey: ["workspace", "options", frequency, workspace.assetSecurityIds, workspace.assetDataInputs, workspace.factorVariantKeys, workspace.signalVersionKeys, workspace.modelTargetKeys],
    queryFn: () => api.workspaceOptions({ frequency, assetSecurityIds: workspace.assetSecurityIds, assetDataInputs: workspace.assetDataInputs, factorVariantKeys: workspace.factorVariantKeys, signalVersionKeys: workspace.signalVersionKeys, modelTargetKeys: workspace.modelTargetKeys }),
    placeholderData: (previous) => previous,
  });
  function selectFrequency(next: Frequency) { const updated = new URLSearchParams(search); updated.set("frequency", next); setSearch(updated, { replace: true }); workspace.setFrequency(next); }
  if (options.isLoading) return <LoadingState />;
  if (options.error) return <ErrorState error={options.error} retry={() => void options.refetch()} />;
  return <div className="page">
    <header className="page-heading"><div><p className="eyebrow">WORKSPACE / MODEL CONTRACTS</p><h1>{chinese ? "模型结构与输入契约" : "Model structures and input contracts"}</h1><p>{chinese ? "每个模型接收全部已选信号，并分别针对所选 Target Kind 与5/21/63 Session期限形成独立输出。" : "Every Model consumes all selected Signals and compiles independently for each selected Target Kind and 5/21/63-session horizon."}</p></div></header>
    <div className="signal-frequency" aria-label={chinese ? "模型频率" : "Model frequency"}>{(["weekly", "monthly"] as const).map((item) => <button className={frequency === item ? "active" : ""} key={item} onClick={() => selectFrequency(item)} type="button">{chinese ? item === "weekly" ? "周频" : "月频" : item}</button>)}</div>
    {options.data && <section className="catalog-section model-target-picker"><div className="section-heading"><div><p className="eyebrow">TARGET KIND / HORIZON</p><h2>{chinese ? "预测目标与期限" : "Prediction targets and horizons"}</h2></div></div><p className="scope-note">{chinese ? "所有选中模型分别使用每个目标组合，形成独立模型输出。目标只允许预设选择。" : "Every selected Model is compiled independently for each selected target preset."}</p><div className="workspace-variant-choices">{options.data.model_target_options.map((target) => { const checked = workspace.modelTargetKeys.includes(target.target_key); return <label className="research-version-row" key={target.target_key}><input type="checkbox" checked={checked} onChange={() => workspace.toggleModelTarget(target.target_key)} /><span><strong>{target.target_kind === "future_return" ? (chinese ? "绝对未来收益" : "Absolute future return") : (chinese ? "横截面相对未来收益" : "Cross-sectional relative future return")}</strong><code>h{target.horizon_sessions} · {target.target_key}</code></span>{target.recommended && <small>{chinese ? "当前频率推荐" : "Recommended"}</small>}</label>; })}</div></section>}
    {options.data && <ModelSelectionCatalog families={options.data.model_families} selected={workspace.modelPresetKeys} toggle={workspace.toggleModel} chinese={chinese} />}
  </div>;
}

function ModelSelectionCatalog({ families, selected, toggle, chinese }: { families: ModelOption[]; selected: string[]; toggle: (key: string) => void; chinese: boolean }) {
  const [query, setQuery] = useState("");
  const term = useDeferredValue(query).trim().toLowerCase();
  const visible = families.map((family) => ({ ...family, presets: family.presets.filter((preset) => !term || [family.key, family.name, family.description, catalogText("model", family.key, true, { name: family.name, description: family.description }).name, preset.preset_key, preset.output_type, preset.output_comparability, preset.target_key ?? "", ...preset.accepted_signal_keys].join(" ").toLowerCase().includes(term)) })).filter((family) => family.presets.length > 0 || !term);
  return <section className="catalog-section workspace-option-section">
    <div className="section-heading"><div><p className="eyebrow">MODEL FAMILIES / PRESETS</p><h2>{chinese ? "模型家族与固定参数预设" : "Model families and fixed presets"}</h2></div><label className="compact-search"><span>{chinese ? "搜索" : "Search"}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={chinese ? "线性、投票、target、信号……" : "linear, vote, target, Signal…"} /></label></div>
    <p className="scope-note">{chinese ? "模型不会选择性丢弃上游信号；任何输入不足、溢出或类型不兼容都会使对应预设变灰。勾选多个模型时分别回测。" : "Models never drop upstream Signals selectively. Missing, overflowing, or incompatible inputs disable the preset. Multiple selected Models are backtested independently."}</p>
    <div className="workspace-family-grid model-family-grid">{visible.map((family) => {
      const text = catalogText("model", family.key, chinese, { name: family.name, description: family.description });
      return <article className={`workspace-family-card research-family-card ${family.implementation_status !== "available" ? "planned" : ""}`} key={family.key}>
        <header><div><span>{contractLabel(family.implementation_status, chinese)}</span><h3>{text.name}</h3><code>{family.key}</code></div></header>
        <p>{text.description}</p>
        <div className="workspace-model-presets">{family.presets.map((preset) => {
          const isSelected = selected.includes(preset.preset_key);
          return <label className={!preset.selectable ? "disabled" : ""} key={preset.preset_key} title={preset.reason_codes.join(", ")}>
            <input type="checkbox" checked={isSelected} disabled={!preset.selectable && !isSelected} onChange={() => toggle(preset.preset_key)} />
            <span><strong>{preset.preset_key}</strong><small>{contractLabel(preset.output_type, chinese)} · {contractLabel(preset.output_comparability, chinese)}</small></span>
            <div className="model-target"><small>{chinese ? "预测 TARGET" : "PREDICTION TARGET"}</small><strong>{chinese ? "使用上方已选目标组合分别编译" : "Compiled once per selected target preset"}</strong><code>target_kind × horizon_sessions</code></div>
            <dl className="research-card-facts">{Object.entries(preset.parameters).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{contractLabel(String(value), chinese)}</dd></div>)}</dl>
            {preset.input_slots.map((slot) => <p className="model-slot" key={slot.slot_key}><strong>{chinese ? "输入槽" : "Input slot"}: {slot.slot_key}</strong><span>{slot.minimum_count}–{slot.maximum_count} {chinese ? "个信号" : "Signals"} · {slot.allowed_output_types.map((item) => contractLabel(item, chinese)).join(" / ")}</span></p>)}
            <p className="workspace-inputs">{preset.selectable ? `${preset.accepted_signal_keys.length} ${chinese ? "个上游信号将全部进入模型" : "upstream Signals accepted in full"}` : preset.reason_codes.map((item) => contractLabel(item, chinese)).join(" · ")}</p>
          </label>;
        })}</div>
      </article>;
    })}</div>
  </section>;
}
