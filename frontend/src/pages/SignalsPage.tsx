import { useMutation, useQuery } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { ErrorState, LoadingState } from "../components/QueryState";
import { catalogText, contractLabel } from "../researchCatalogText";
import { useWorkspaceSelection } from "../workspace/WorkspaceSelectionContext";

type Frequency = "weekly" | "monthly";
type SignalOption = Awaited<ReturnType<typeof api.workspaceOptions>>["signal_families"][number];

function exportStatusLabel(status: string, chinese: boolean) {
  if (!chinese) return status;
  return ({ queued: "已排队", running: "正在孵化", completed: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status;
}

export function SignalsPage() {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const [search, setSearch] = useSearchParams();
  const workspace = useWorkspaceSelection();
  const frequency: Frequency = workspace.frequency;
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const downloadedExport = useRef<string | null>(null);
  const serializedSearch = search.toString();
  useEffect(() => {
    const updated = new URLSearchParams(serializedSearch);
    if (updated.get("frequency") === workspace.frequency) return;
    updated.set("frequency", workspace.frequency);
    setSearch(updated, { replace: true });
  }, [serializedSearch, setSearch, workspace.frequency]);
  const exportSignals = useMutation({
    mutationFn: () => api.exportSelectedSignals({ frequency, assetSecurityIds: workspace.assetSecurityIds, assetDataInputs: workspace.assetDataInputs, signalVersionKeys: workspace.signalVersionKeys, includeTargets: true }),
    onSuccess: (job) => {
      downloadedExport.current = null;
      setExportJobId(job.export_job_id);
    },
  });
  const exportJob = useQuery({
    queryKey: ["signal-research-export", exportJobId],
    queryFn: () => api.signalExportStatus(exportJobId!),
    enabled: Boolean(exportJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["completed", "failed", "cancelled"].includes(status) ? false : 1000;
    },
  });
  useEffect(() => {
    const job = exportJob.data;
    if (!job?.download_url || job.status !== "completed" || downloadedExport.current === job.export_job_id) return;
    downloadedExport.current = job.export_job_id;
    const anchor = document.createElement("a");
    anchor.href = job.download_url;
    anchor.download = "migratory_bird_signal_research.zip";
    anchor.click();
  }, [exportJob.data]);
  const options = useQuery({
    queryKey: ["workspace", "options", frequency, workspace.assetSecurityIds, workspace.assetDataInputs, workspace.factorVariantKeys, workspace.signalVersionKeys],
    queryFn: () => api.workspaceOptions({ frequency, assetSecurityIds: workspace.assetSecurityIds, assetDataInputs: workspace.assetDataInputs, factorVariantKeys: workspace.factorVariantKeys, signalVersionKeys: workspace.signalVersionKeys }),
    placeholderData: (previous) => previous,
  });
  function selectFrequency(next: Frequency) {
    const updated = new URLSearchParams(search); updated.set("frequency", next);
    setSearch(updated, { replace: true }); workspace.setFrequency(next);
  }
  if (options.isLoading) return <LoadingState />;
  if (options.error) return <ErrorState error={options.error} retry={() => void options.refetch()} />;
  return <div className="page">
    <header className="page-heading"><div><p className="eyebrow">WORKSPACE / SIGNAL CATALOG</p><h1>{chinese ? "信号库与合法输入" : "Signal catalog and legal inputs"}</h1><p>{chinese ? "信号卡说明经济含义、方向、输出形式和因子来源。先勾选因子参数，再选择对应信号版本；已选择版本可直接下载已发布信号值。" : "Signal cards explain economic meaning, direction, output form, and Factor source. Select Factor variants first, then choose and download published Signal versions."}</p></div></header>
    <div className="signal-frequency" aria-label={chinese ? "信号频率" : "Signal frequency"}>{(["weekly", "monthly"] as const).map((item) => <button className={frequency === item ? "active" : ""} key={item} onClick={() => selectFrequency(item)} type="button">{chinese ? item === "weekly" ? "周频" : "月频" : item}</button>)}</div>
    {options.data && <SignalSelectionCatalog families={options.data.signal_families} selected={workspace.signalVersionKeys} toggle={workspace.toggleSignal} chinese={chinese} />}
    <section className="signal-export-bar">
      <div><strong>{chinese ? `已选择 ${workspace.signalVersionKeys.length} 个信号版本` : `${workspace.signalVersionKeys.length} Signal versions selected`}</strong><p>{chinese ? "仅导出所选信号；训练Target位于独立Parquet文件中，不会作为模型输入。" : "Exports selected Signals only; training labels live in a separate Parquet file."}</p></div>
      <button type="button" disabled={!workspace.assetSecurityIds.length || !workspace.signalVersionKeys.length || exportSignals.isPending || exportJob.data?.status === "queued" || exportJob.data?.status === "running"} onClick={() => exportSignals.mutate()}>{exportSignals.isPending ? (chinese ? "正在提交…" : "Submitting…") : (chinese ? "导出全部已选信号" : "Export selected Signals")}</button>
      {exportJobId && exportJob.data && <div className="signal-export-progress" role="status">
        <progress aria-label={chinese ? "导出进度" : "Export progress"} {...(exportJob.data.status === "completed" ? { value: 1, max: 1 } : {})} />
        <span>{chinese ? `导出状态：${exportStatusLabel(exportJob.data.status, true)}` : `Export status: ${exportStatusLabel(exportJob.data.status, false)}`}</span>
      </div>}
      {(exportSignals.error || exportJob.error) && <span className="workspace-invalid">{String(exportSignals.error ?? exportJob.error)}</span>}
      {exportJob.data?.status === "failed" && <span className="workspace-invalid">{String(exportJob.data.failure_details?.message ?? exportJob.data.failure_class ?? "Export failed")}</span>}
      {exportJob.data?.status === "cancelled" && <span className="workspace-invalid">{chinese ? "导出任务已取消，可以重新提交。" : "Export was cancelled. You may submit it again."}</span>}
    </section>
  </div>;
}

function SignalSelectionCatalog({ families, selected, toggle, chinese }: { families: SignalOption[]; selected: string[]; toggle: (key: string) => void; chinese: boolean }) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string[]>([]);
  const term = useDeferredValue(query).trim().toLowerCase();
  const visible = families.filter((family) => !term || [family.key, family.economic_family, family.dimension_hint, family.rationale, catalogText("signal", family.key, true, { name: family.key, description: family.rationale }).name, ...family.versions.flatMap((version) => [version.version_key, version.factor_variant_key])].join(" ").toLowerCase().includes(term));
  return <section className="catalog-section workspace-option-section">
    <div className="section-heading"><div><p className="eyebrow">LEGAL SIGNAL FAMILIES</p><h2>{chinese ? "信号家族与参数版本" : "Signal families and parameter versions"}</h2></div><label className="compact-search"><span>{chinese ? "搜索" : "Search"}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={chinese ? "动量、波动率、因子参数……" : "momentum, volatility, Factor variant…"} /></label></div>
    <p className="scope-note">{chinese ? "同一家族只显示一张卡。上游因子没有被勾选的版本会保留但变灰；系统不会静默替换参数。" : "One card per family. Versions unsupported by selected Factors remain visible but disabled; parameters are never silently replaced."}</p>
    <div className="workspace-family-grid">{visible.map((family) => {
      const text = catalogText("signal", family.key, chinese, { name: family.key, description: family.rationale });
      const open = expanded.includes(family.key);
      const selectedCount = family.versions.filter((item) => selected.includes(item.version_key)).length;
      return <article className="workspace-family-card research-family-card" key={family.key}>
        <button className="research-card-toggle" type="button" aria-expanded={open} onClick={() => setExpanded((items) => items.includes(family.key) ? items.filter((item) => item !== family.key) : [...items, family.key])}>
          <span><small>{contractLabel(family.economic_family, chinese)} · {contractLabel(family.dimension_hint, chinese)}</small><strong>{text.name}</strong><code>{family.key}</code></span><b>{selectedCount ? `${selectedCount}/${family.versions.length}` : family.versions.length} {open ? "−" : "+"}</b>
        </button>
        <p>{text.description}</p>
        <div className="research-card-tags"><span>{contractLabel(family.direction, chinese)}</span><span>{contractLabel(family.output_type, chinese)}</span><span>{contractLabel(family.rationale_type, chinese)}</span><span>{contractLabel(family.research_tier, chinese)}</span><span>{family.product_eligible ? (chinese ? "可进入产品研究" : "product eligible") : (chinese ? "仅探索诊断" : "diagnostic only")}</span></div>
        {open && <div className="research-card-detail">
          {family.rule && <dl className="research-card-facts">{Object.entries(family.rule).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>}
          <div className="workspace-variant-choices">{family.versions.map((version) => {
            const isSelected = selected.includes(version.version_key);
            return <div className={`research-version-row ${!version.selectable ? "disabled" : ""}`} key={version.version_key} title={version.reason_codes.join(", ")}>
              <label><input type="checkbox" checked={isSelected} disabled={!version.selectable && !isSelected} onChange={() => toggle(version.version_key)} /><span><strong>{version.factor_variant_key}</strong><code>{version.version_key}</code></span></label>
              {isSelected && <a className="factor-download" download href={`/api/v2/signals/versions/${encodeURIComponent(version.version_key)}/download.csv`}>{chinese ? "下载信号 CSV" : "Download Signal CSV"}</a>}
            </div>;
          })}</div>
        </div>}
      </article>;
    })}</div>
  </section>;
}
