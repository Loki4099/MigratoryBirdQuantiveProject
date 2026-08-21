import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";

import {
  api,
  ApiClientError,
  type GraphChangePreviewResponse,
  type GraphSuiteRuntimeReadinessResponse,
} from "../api/client";
import {
  FormulaDisplay,
  researchLabel,
  researchDescription,
  researchRationale,
} from "../components/ResearchText";
import {
  type GraphDerivedView,
  useGraphDraft,
} from "../workspace/GraphDraftContext";

type StageNo = 0 | 1 | 2 | 3;
type StageTab = StageNo | "aggregation" | "strategy";
export type GraphWorkspaceView = "context" | 1 | 2 | 3 | "aggregation" | "strategy" | "launch";
type StageView = GraphDerivedView["stages"][number];
type Family = StageView["families"][number];
type Occurrence = Family["variants"][number];
type GraphStrategyOption = GraphDerivedView["strategies"][number];
type GraphAggregationOption = GraphDerivedView["aggregations"][number];
type GraphBlocker = GraphDerivedView["blockers"][number];

interface AggregationPresetDefinition {
  preset_key: string;
  name: string;
  description: string;
  version_number: number;
  semantics: Record<string, unknown>;
  selected: boolean;
  selectable: boolean;
  reason_codes: string[];
}

interface AggregationAxisDefinition {
  key: string;
  name: string;
  description: string;
  version_number: number;
  semantics: Record<string, unknown>;
  selected: boolean;
}

type ExplainedAggregationOption = GraphAggregationOption & {
  algorithm_identity?: string;
  objective_semantics?: Record<string, unknown>;
  output_semantics?: Record<string, unknown>;
  execution_mode?: string;
  input_payload_contract_key?: string;
  output_payload_contract_key?: string;
  ordering_policy?: string;
  input_policy?: Record<string, unknown>;
  compatibility_policy?: Record<string, unknown>;
  missing_policy?: Record<string, unknown>;
  tie_policy?: Record<string, unknown>;
  parameter_preset_definitions?: AggregationPresetDefinition[];
  targets?: AggregationAxisDefinition[];
  selected_targets?: string[];
  training_presets?: AggregationAxisDefinition[];
  selected_training_presets?: string[];
  internal_member_count?: number;
};

interface StrategyParameterPresetOption {
  preset_key: string;
  name: string;
  description: string;
  version_number: number;
  parameters: Record<string, unknown>;
  selected: boolean;
  selectable: boolean;
  reason_codes: string[];
}

type TransitionalStrategyOption = GraphStrategyOption & {
  parameter_presets?: StrategyParameterPresetOption[];
  selection_semantics?: Record<string, unknown>;
  research_hypothesis?: string;
  input_payload_contract_key?: string;
  schedule_policy?: Record<string, unknown>;
  execution_policy?: Record<string, unknown>;
};
type StrategyOption = Omit<TransitionalStrategyOption, "parameter_presets"> & {
  parameter_presets: StrategyParameterPresetOption[];
};

interface StrategyFamily {
  familyKey: string;
  name: string;
  selected: boolean;
  variants: StrategyOption[];
}

type ExplainedDefenseOption = GraphDerivedView["defenses"][number] & {
  allocation_semantics?: Record<string, unknown>;
  research_hypothesis?: string;
  input_policy?: Record<string, unknown>;
  allocation_policy_document?: Record<string, unknown>;
  timing_policy?: (NonNullable<GraphDerivedView["defenses"][number]["timing_policy"]> & {
    formula_identity?: string;
    research_hypothesis?: string;
  }) | null;
  allocation_policy?: (NonNullable<GraphDerivedView["defenses"][number]["allocation_policy"]> & {
    formula_identity?: string;
    research_hypothesis?: string;
  }) | null;
};

const stageNames = ["Raw Input", "Processing 1", "Processing 2", "Processing 3"] as const;

export function GraphWorkspacePage({ view }: { view: GraphWorkspaceView }) {
  const { i18n } = useTranslation();
  const chinese = (i18n.resolvedLanguage ?? "zh-CN") === "zh-CN";
  const location = useLocation();
  const navigate = useNavigate();
  const graph = useGraphDraft();
  const [inspected, setInspected] = useState<Occurrence | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [availability, setAvailability] = useState<
    "all" | "ready" | "requires_ancestors" | "hard_incompatible"
  >("all");
  const handledViewConflict = useRef<unknown>(null);
  // The current v0.22 research mainline intentionally exposes only the
  // no-defense branch. Filtering here also protects users opening an older
  // persisted Draft projection that still contains retired defense cards.
  const data = graph.derived
    ? { ...graph.derived, defenses: graph.derived.defenses.filter((item) => item.variant_key === "none") }
    : undefined;
  const retiredDefenseSelected = graph.derived?.defenses.some((item) => (
    item.selected && item.variant_key !== "none"
  )) ?? false;
  const frequencyValue = graph.snapshot?.intent?.frequency;
  const frequency = frequencyValue === "weekly" || frequencyValue === "monthly"
    ? frequencyValue
    : undefined;
  const aggregations = data?.aggregations
    .filter((item) => item.selected)
    .map((item) => item.family_key) ?? [];
  const selectedComposedDefenseCount = data?.defenses.filter((item) => (
    item.selected && item.variant_key !== "none" && item.composed
  )).length ?? 0;
  const currentCompileFingerprint = graph.lastCompile
    && graph.lastCompile.graph_draft_id === graph.snapshot?.graph_draft_id
    && graph.lastCompile.graph_draft_revision === graph.snapshot?.revision
    && graph.lastCompile.compiled_execution_data_context_id
    && graph.lastCompile.execution_data_context_artifact_id
    && /^[0-9a-f]{64}$/.test(graph.lastCompile.execution_data_context_fingerprint ?? "")
    && typeof graph.lastCompile.execution_data_context_reused === "boolean"
    && graph.lastCompile.selection_fingerprint === data?.selection_fingerprint
    && (graph.lastCompile.defense_execution_contexts ?? []).length
      === selectedComposedDefenseCount
    ? graph.lastCompile.graph_fingerprint
    : null;
  const mutationDisabled = graph.locked
    || graph.busy
    || graph.pendingCommandCount > 0
    || graph.queuePaused
    || Boolean(graph.pendingImpact);
  const runtimeReadiness = useQuery({
    queryKey: ["v022", "graph-suite-runtime", "readiness"],
    queryFn: api.graphSuiteRuntimeReadiness,
    enabled: view === "launch",
    refetchInterval: view === "launch" ? 3_000 : false,
    retry: false,
  });
  const stageNo: StageNo = view === "context" ? 0 : typeof view === "number" ? view : 3;
  const pageCopy = graphPageCopy(view, chinese);
  const navigateToView = (target: StageTab) => {
    const prefix = location.pathname.startsWith("/workspace-v022/") ? "/workspace-v022" : "";
    const path = target === 0 ? "context"
      : typeof target === "number" ? `processing-${target}`
      : target === "strategy" ? (prefix ? "strategy" : "strategy-configuration")
      : "aggregation";
    navigate(`${prefix}/${path}${location.search}`);
  };
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const familyQuery = useInfiniteQuery({
    queryKey: [
      "workspaceGraph",
      "stage",
      graph.snapshot?.graph_draft_id,
      graph.snapshot?.revision,
      stageNo,
      debouncedSearch,
      availability,
    ],
    queryFn: ({ pageParam }) => api.graphStageFamilies(
      String(graph.snapshot?.graph_draft_id),
      {
        stageNo,
        search: debouncedSearch,
        availabilityFilter: availability,
        cursor: pageParam,
        // The v0.22 Catalog is intentionally compact. Load the complete stage so
        // classic signals are never hidden behind an unexplained first page.
        limit: 50,
      },
    ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: typeof view === "number" && Boolean(graph.snapshot),
    retry: false,
  });
  useEffect(() => {
    if (
      familyQuery.error instanceof ApiClientError
      && familyQuery.error.code === "workspace_view_token_conflict"
      && handledViewConflict.current !== familyQuery.error
    ) {
      handledViewConflict.current = familyQuery.error;
      void graph.reload();
    }
  }, [familyQuery.error, graph]);
  const pinnedFamilies = familyQuery.data?.pages[0]?.pinned_families ?? [];
  const catalogFamilies = familyQuery.data?.pages.flatMap(
    (page) => page.catalog_families,
  ) ?? [];

  const toggle = (item: Occurrence) => {
    if (mutationDisabled) return;
    if (item.availability === "hard_incompatible" && !item.is_explicit) return;
    void graph.toggleFeature(item.feature_key, item.stage_no, item.is_explicit);
  };
  const changeAggregation = (next: string[]) => {
    if (mutationDisabled) return;
    const changed = [...new Set([...aggregations, ...next])]
      .find((key) => aggregations.includes(key) !== next.includes(key));
    if (changed) void graph.toggleAggregation(changed, aggregations.includes(changed));
  };
  const changeAggregationPresets = (familyKey: string, next: string[]) => {
    if (mutationDisabled) return;
    void graph.setAggregationPresets(familyKey, next);
  };
  const changeAggregationTargets = (familyKey: string, next: string[]) => {
    if (mutationDisabled) return;
    void graph.setAggregationTargets(familyKey, next);
  };
  const changeAggregationTrainingPresets = (familyKey: string, next: string[]) => {
    if (mutationDisabled) return;
    void graph.setAggregationTrainingPresets(familyKey, next);
  };

  return <div className="page graph-workspace-page">
    <header className="page-heading graph-workspace-heading">
      <div>
        <p className="eyebrow">{chinese ? "当前研究 / 配置工作流" : "CURRENT RESEARCH / CONFIGURATION WORKFLOW"}</p>
        <h1>{pageCopy.title}</h1>
        <p>{pageCopy.subtitle}</p>
      </div>
      <span className={`graph-api-state ${graph.loading || graph.busy || graph.pendingCommandCount ? "pending" : "ready"}`}>
        {graph.queuePaused
          ? (chinese ? "队列已暂停" : "Queue paused")
          : graph.pendingCommandCount
          ? `${chinese ? "等待服务器确认" : "Pending commands"}: ${graph.pendingCommandCount}`
          : graph.loading || graph.busy
          ? (chinese ? "保存中" : "Saving")
          : `${chinese ? "已保存" : "Saved"} · ${chinese ? "版本" : "revision"} ${graph.snapshot?.revision ?? "—"}`}
      </span>
    </header>

    <section className="graph-context-strip">
      <button
        type="button"
        className="danger"
        disabled={graph.busy || graph.pendingCommandCount > 0 || !graph.snapshot}
        onClick={() => setResetOpen(true)}
      >{chinese ? "重置当前研究" : "Reset current research"}</button>
      <label>{chinese ? "频率" : "Frequency"}
        <select
          value={frequency ?? "weekly"}
          disabled={!frequency || mutationDisabled}
          onChange={(event) => void graph.setFrequency(
            event.target.value as "weekly" | "monthly",
          )}
        >
          <option value="weekly">{chinese ? "每周" : "Weekly"}</option>
          <option value="monthly">{chinese ? "每月" : "Monthly"}</option>
        </select>
      </label>
      <Metric label={chinese ? "候选资产" : "Candidate assets"} value={assetContextMemberIds(graph.snapshot?.asset_context).length} />
      <Metric label={chinese ? "最终信号" : "Final signals"} value={data?.summary.stage3_input_count ?? 0} />
      <Metric label={chinese ? "实验分支" : "Experiment branches"} value={data?.summary.strategy_branch_count ?? 0} />
    </section>

    {graph.locked ? <section className="graph-message">
      <strong>{chinese ? "当前研究已由实验冻结" : "Current research locked by an experiment"}</strong>
      <span>{chinese
        ? "资产、加工层、聚合与策略设置现在只读。需要更换配置时，请重置当前研究。"
        : "Assets, processing, aggregation, and strategy settings are read-only. Reset the current research to change them."}</span>
    </section> : null}

    {graph.error ? <section className="graph-message error">
      <strong>{view === "launch"
        ? (chinese ? "实验启动失败" : "Experiment launch failed")
        : (chinese ? "研究配置保存失败" : "Research configuration failed")}</strong>
      <span>{graph.error}</span>
      {graph.queuePaused ? <button type="button" onClick={() => void graph.reload()}>{chinese ? "重新加载并继续队列" : "Reload and resume queue"}</button> : null}
    </section> : null}
    {graph.loading ? <section className="graph-message"><strong>{chinese ? "正在恢复当前研究" : "Restoring current research"}</strong></section> : null}
    {view === "context" ? <UniverseBuilder chinese={chinese} /> : null}
    {typeof view === "number" && data ? <>
      <div className="graph-variant-actions graph-page-bulk-actions">
        <button type="button" disabled={mutationDisabled} onClick={() => void graph.selectAllStage(stageNo)}>
          {chinese ? "全选本层合法项" : "Select all legal options"}
        </button>
        <button type="button" disabled={mutationDisabled} onClick={() => void graph.clearStage(stageNo)}>
          {chinese ? "清空本层主动选择" : "Clear explicit selections"}
        </button>
      </div>
      <StagePanel
      stage={data.stages[stageNo]}
      pinned={pinnedFamilies}
      catalog={catalogFamilies}
      totalCatalog={familyQuery.data?.pages[0]?.total_catalog_family_count ?? 0}
      search={search}
      availability={availability}
      loading={familyQuery.isLoading}
      loadingMore={familyQuery.isFetchingNextPage}
      hasMore={familyQuery.hasNextPage}
      error={familyQuery.error instanceof Error ? familyQuery.error.message : null}
      pendingLabels={new Set(graph.pendingOccurrences)}
      chinese={chinese}
      onSearch={setSearch}
      onAvailability={setAvailability}
      onLoadMore={() => void familyQuery.fetchNextPage()}
      onRetry={() => void familyQuery.refetch()}
      onToggle={toggle}
      onBatchSelect={(items) => void graph.selectFeatureBatch(items.map((item) => ({ featureKey: item.feature_key, stageNo: item.stage_no })))}
      onInspect={setInspected}
      />
    </> : null}
    {view === "aggregation" && data ? <AggregationPanel data={data} selected={aggregations} onChange={changeAggregation} onPresetChange={changeAggregationPresets} onTargetChange={changeAggregationTargets} onTrainingChange={changeAggregationTrainingPresets} chinese={chinese} /> : null}
    {view === "strategy" && data ? <div className="graph-strategy-flow">
      {retiredDefenseSelected ? <section className="graph-error">
        <strong>{chinese ? "旧防御方案已退役" : "The prior defense package is retired"}</strong>
        <span>{chinese ? "请点击下方“使用不防御”保存当前主线配置，然后重新编译。" : "Use the no-defense action below to save the supported mainline before compiling again."}</span>
      </section> : null}
      <ReviewPanel
        data={data}
        chinese={chinese}
        onNavigate={navigateToView}
        onCompile={() => void graph.compile().then((compiled) => {
          if (!compiled) return;
          navigate(location.pathname.startsWith("/workspace-v022/")
            ? `/workspace-v022/launch${location.search}`
            : `/experiment-launch${location.search}`);
        })}
        onProceed={() => navigate(location.pathname.startsWith("/workspace-v022/")
          ? `/workspace-v022/launch${location.search}`
          : `/experiment-launch${location.search}`)}
        disabled={mutationDisabled || retiredDefenseSelected}
        compiledFingerprint={currentCompileFingerprint}
      />
      <StrategyDefensePanel
        data={data}
        chinese={chinese}
        pendingLabels={new Set(graph.pendingOccurrences)}
        onStrategyPresets={(key, presetKeys) => void graph.setStrategyPresets(key, presetKeys)}
        onSelectAllStrategies={() => void graph.selectAllStrategies()}
        onClearStrategies={() => void graph.clearStrategies()}
        onDefense={(key, selected) => void graph.toggleDefense(key, selected)}
        onClearDefenses={() => void graph.clearDefenses()}
      />
    </div> : null}
    {view === "launch" && data ? <ExperimentLaunchPanel
      data={data}
      chinese={chinese}
      compiledFingerprint={currentCompileFingerprint}
      busy={graph.busy}
      runtimeReadiness={runtimeReadiness.data ?? null}
      runtimeReadinessLoading={runtimeReadiness.isLoading}
      runtimeReadinessError={runtimeReadiness.error instanceof Error
        ? runtimeReadiness.error.message
        : null}
      onBack={() => navigate(location.pathname.startsWith("/workspace-v022/")
        ? `/workspace-v022/strategy${location.search}`
        : `/strategy-configuration${location.search}`)}
      onStart={(frequencies) => void graph.submitLaunchBatch(frequencies).then((batch) => {
        if (!batch) return;
        const params = new URLSearchParams(location.search);
        params.delete("graph_suite");
        params.set("launch_batch", batch.suite_launch_batch_id);
        params.set("contract", "v0.22");
        navigate(`/experiments?${params.toString()}`);
      })}
    /> : null}
    {inspected ? <LineageDrawer item={inspected} chinese={chinese} onClose={() => setInspected(null)} /> : null}
    {graph.pendingImpact ? <CascadeDialog
      preview={graph.pendingImpact}
      chinese={chinese}
      busy={graph.busy}
      onCancel={graph.cancelImpact}
      onConfirm={() => void graph.confirmImpact()}
    /> : null}
    {resetOpen ? <ResetResearchDialog
      chinese={chinese}
      busy={graph.busy}
      onCancel={() => setResetOpen(false)}
      onConfirm={() => void graph.resetCurrentResearch().then(() => {
        setResetOpen(false);
        navigate("/workspace-v022/context", { replace: true });
      })}
    /> : null}
  </div>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

type AssetCatalogItem = Awaited<ReturnType<typeof api.allAssets>>["items"][number];
type CandidateGroup = "stock" | "fund";
type AssetFilter = "eligible" | CandidateGroup | "other";

function UniverseBuilder({ chinese }: { chinese: boolean }) {
  const graph = useGraphDraft();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<AssetFilter | null>(null);
  const [savingSelection, setSavingSelection] = useState(false);
  const [saveResult, setSaveResult] = useState<"idle" | "saved" | "failed">("idle");
  const [exportFormat, setExportFormat] = useState<"parquet" | "csv">("parquet");
  const [exportStart, setExportStart] = useState("");
  const [exportEnd, setExportEnd] = useState("");
  const [exportPreview, setExportPreview] = useState<Awaited<
    ReturnType<typeof api.previewAssetDataExport>
  > | null>(null);
  const [previewedExportInput, setPreviewedExportInput] = useState<Parameters<
    typeof api.previewAssetDataExport
  >[0] | null>(null);
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const currentIds = useMemo(() => assetContextMemberIds(graph.snapshot?.asset_context), [
    graph.snapshot?.asset_context,
  ]);
  const currentKey = `${graph.snapshot?.graph_draft_id ?? ""}:${graph.snapshot?.revision ?? 0}`;
  const [selectionDraft, setSelectionDraft] = useState<{ key: string; ids: string[] }>({
    key: currentKey,
    ids: currentIds,
  });
  const selectedIds = selectionDraft.key === currentKey ? selectionDraft.ids : currentIds;
  const setSelectedIds = (next: string[] | ((current: string[]) => string[])) => {
    setSaveResult("idle");
    setExportPreview(null);
    setPreviewedExportInput(null);
    setExportError(null);
    setSelectionDraft({
      key: currentKey,
      ids: typeof next === "function" ? next(selectedIds) : next,
    });
  };
  const assets = useQuery({
    queryKey: ["catalog", "assets", "v022-universe-builder"],
    queryFn: () => api.allAssets(),
    enabled: Boolean(graph.snapshot),
    retry: false,
  });
  const exportStatus = useQuery({
    queryKey: ["asset-data-export", exportJobId],
    queryFn: () => api.assetDataExportStatus(exportJobId!),
    enabled: Boolean(exportJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["completed", "failed", "cancelled"].includes(status) ? false : 2_000;
    },
    retry: false,
  });
  const exportInProgress = ["queued", "running"].includes(exportStatus.data?.status ?? "");
  const activeExportPreview = previewedExportInput && graph.snapshot
    && previewedExportInput.graphDraftId === graph.snapshot.graph_draft_id
    && previewedExportInput.graphDraftRevision === graph.snapshot.revision
    ? exportPreview
    : null;
  const items = useMemo(() => assets.data?.items ?? [], [assets.data?.items]);
  const byId = useMemo(() => new Map(items.map((item) => [item.security_id, item])), [items]);
  const selectedGroup = selectedIds
    .map((id) => byId.get(id))
    .map((item) => item && candidateGroup(item))
    .find((group): group is CandidateGroup => Boolean(group));
  const activeFilter = filter ?? selectedGroup ?? "eligible";
  const activeCandidateGroup = activeFilter === "stock" || activeFilter === "fund"
    ? activeFilter
    : selectedGroup;
  const visible = items.filter((item) => {
    const group = candidateGroup(item);
    const eligible = isV022Candidate(item);
    if (activeFilter === "eligible" && !eligible) return false;
    if ((activeFilter === "stock" || activeFilter === "fund") && group !== activeFilter) return false;
    if (activeFilter === "other" && group) return false;
    const needle = search.trim().toLocaleLowerCase();
    return !needle || [item.symbol, item.name, item.asset_key, ...item.aliases]
      .some((value) => value.toLocaleLowerCase().includes(needle));
  });
  const changed = [...selectedIds].sort().join("|") !== [...currentIds].sort().join("|");
  const saveSelection = async () => {
    setSavingSelection(true);
    setSaveResult("idle");
    const saved = await graph.setAssetSelection(selectedIds);
    setSaveResult(saved ? "saved" : "failed");
    setSavingSelection(false);
  };
  const exportInput = (
    format: "parquet" | "csv" = exportFormat,
    startDate = exportStart,
    endDate = exportEnd,
  ) => ({
    graphDraftId: graph.snapshot!.graph_draft_id,
    graphDraftRevision: graph.snapshot!.revision,
    exportFormat: format,
    startDate: startDate || undefined,
    endDate: endDate || undefined,
  });
  const previewExport = async (input = exportInput()) => {
    if (!graph.snapshot) return;
    setExportBusy(true);
    setExportError(null);
    try {
      const preview = await api.previewAssetDataExport(input);
      setPreviewedExportInput(input);
      setExportPreview(preview);
    } catch (error) {
      setExportPreview(null);
      setPreviewedExportInput(null);
      setExportError(error instanceof Error ? error.message : String(error));
    } finally {
      setExportBusy(false);
    }
  };
  const previewFullExport = async () => {
    if (!graph.snapshot) return;
    setExportFormat("parquet");
    setExportStart("");
    setExportEnd("");
    await previewExport(exportInput("parquet", "", ""));
    window.requestAnimationFrame(() => document.getElementById("asset-data-export")
      ?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
  };
  const startExport = async () => {
    if (!graph.snapshot || !activeExportPreview || !previewedExportInput) return;
    setExportBusy(true);
    setExportError(null);
    try {
      const job = await api.createAssetDataExport(previewedExportInput);
      setExportJobId(job.export_job_id);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : String(error));
    } finally {
      setExportBusy(false);
    }
  };
  const selectedLabel = selectedGroup === "stock"
    ? (chinese ? "股票 / ADR" : "Stocks / ADRs")
    : selectedGroup === "fund"
      ? (chinese ? "ETF / ETP" : "ETFs / ETPs")
      : (chinese ? "尚未确定" : "Not selected");
  const toggle = (item: AssetCatalogItem) => {
    const checked = selectedIds.includes(item.security_id);
    if (checked) {
      setSelectedIds((ids) => ids.filter((id) => id !== item.security_id));
      return;
    }
    if (!isV022Candidate(item)) return;
    const group = candidateGroup(item);
    if (selectedGroup && group !== selectedGroup) return;
    setSelectedIds((ids) => [...ids, item.security_id]);
  };

  return <section className="universe-builder" aria-label={chinese ? "研究资产选择" : "Research asset selection"}>
    <header className="universe-builder-heading">
      <div>
        <p className="eyebrow">ASSET UNIVERSE / CANDIDATE SELECTION</p>
        <h2>{chinese ? "选择参与信号排名与持仓构建的资产" : "Choose assets used for signal ranking and portfolio construction"}</h2>
        <p>{chinese
          ? "当前版本支持同一实验内选择股票/ADR，或选择 ETF/ETP。标普 500 基准、利率和防御资产由各自契约独立冻结，不会占用候选资产名额。"
          : "This version supports either stocks/ADRs or ETFs/ETPs within one experiment. The S&P 500 benchmark, rates and defensive assets are frozen independently."}</p>
      </div>
      <div className="universe-selection-summary">
        <span>{chinese ? "当前候选类型" : "Candidate type"}</span>
        <strong>{selectedLabel}</strong>
        <code>{selectedIds.length} {chinese ? "项" : "assets"}</code>
        <button
          type="button"
          className="asset-export-primary-action"
          disabled={changed || !currentIds.length || exportBusy || exportInProgress}
          onClick={() => void previewFullExport()}
        >{exportInProgress
            ? (chinese ? "导出任务进行中" : "Export in progress")
            : exportBusy
              ? (chinese ? "正在检查导出范围…" : "Checking export scope…")
              : (chinese ? "导出全部已选资产数据" : "Export all selected asset data")}</button>
        <small>{changed
          ? (chinese ? "请先保存资产选择" : "Save the asset selection first")
          : (chinese
              ? `已保存 ${currentIds.length} 项 · 完整日期 · Parquet ZIP`
              : `${currentIds.length} saved · full history · Parquet ZIP`)}</small>
      </div>
    </header>

    <div className="universe-controls">
      <label>
        <span>{chinese ? "搜索资产" : "Search assets"}</span>
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={chinese ? "代码、名称或资产 key" : "Symbol, name or asset key"}
        />
      </label>
      <nav aria-label={chinese ? "资产类型" : "Asset type"}>
        {([
          ["eligible", chinese ? "当前共同数据集可选" : "Selectable in the shared dataset"],
          ["stock", chinese ? "股票 / ADR" : "Stocks / ADRs"],
          ["fund", chinese ? "ETF / ETP" : "ETFs / ETPs"],
          ["other", chinese ? "参考与后续资产" : "Reference / future"],
        ] as Array<[AssetFilter, string]>).map(([key, label]) => <button
          type="button"
          className={activeFilter === key ? "active" : ""}
          key={key}
          onClick={() => setFilter(key)}
        >{label}</button>)}
      </nav>
      <div className="universe-actions">
        <button
          type="button"
          disabled={!activeCandidateGroup || graph.busy || graph.locked}
          onClick={() => setSelectedIds(items
            .filter((item) => isV022Candidate(item) && candidateGroup(item) === activeCandidateGroup)
            .map((item) => item.security_id))}
        >{chinese ? "全选当前共同数据集" : "Select the shared executable dataset"}</button>
        <button
          type="button"
          disabled={!selectedIds.length || graph.busy || graph.locked}
          onClick={() => setSelectedIds([])}
        >{chinese ? "清空候选" : "Clear candidates"}</button>
        <button type="button" disabled={!changed || graph.busy} onClick={() => setSelectedIds(currentIds)}>
          {chinese ? "恢复当前修订" : "Reset revision"}
        </button>
        <button
          type="button"
          disabled={!changed || selectedIds.length < 2 || graph.busy || graph.locked || savingSelection}
          onClick={() => void saveSelection()}
        >{savingSelection || graph.busy
            ? (chinese ? "正在保存…" : "Saving…")
            : (chinese ? "保存资产选择" : "Save asset selection")}</button>
      </div>
      <p className="universe-save-note" role="status" aria-live="polite">{savingSelection || graph.busy
        ? (chinese ? "正在保存资产选择…" : "Saving asset selection…")
        : saveResult === "failed"
          ? (chinese ? "资产选择未保存，请查看页面顶部错误信息。" : "Asset selection was not saved. Check the error above.")
          : saveResult === "saved" || !changed
            ? (chinese
              ? `资产选择已保存到版本 ${graph.snapshot?.revision ?? "—"}`
              : `Asset selection saved in revision ${graph.snapshot?.revision ?? "—"}`)
            : (chinese
              ? "当前选择尚未保存；确认后请点击“保存资产选择”。"
              : "Current selection is not saved. Confirm it, then choose Save asset selection.")}</p>
    </div>

    <section id="asset-data-export" className="asset-export-panel" aria-label={chinese ? "资产数据导出" : "Asset data export"}>
      <header>
        <div>
          <p className="eyebrow">SAVED UNIVERSE / DATA PACKAGE</p>
          <h3>{chinese ? "导出当前已保存资产的数据" : "Export data for the saved asset universe"}</h3>
          <p>{chinese
            ? "导出使用当前修订冻结的数据集与质量门。大型 Parquet 包按年份分区，并同时复制到下载文件夹，避免 OneDrive 同步。"
            : "Exports use the dataset and quality gate frozen by this revision. Large Parquet packages are partitioned by year and copied to Downloads outside OneDrive."}</p>
        </div>
      </header>
      <div className="asset-export-controls">
        <label><span>{chinese ? "格式" : "Format"}</span><select value={exportFormat} onChange={(event) => {
          setExportFormat(event.target.value as "parquet" | "csv");
          setExportPreview(null);
          setPreviewedExportInput(null);
        }}><option value="parquet">Parquet ZIP</option><option value="csv">CSV ZIP</option></select></label>
        <label><span>{chinese ? "开始日期（可选）" : "Start date (optional)"}</span><input type="date" value={exportStart} onChange={(event) => {
          setExportStart(event.target.value);
          setExportPreview(null);
          setPreviewedExportInput(null);
        }} /></label>
        <label><span>{chinese ? "结束日期（可选）" : "End date (optional)"}</span><input type="date" value={exportEnd} onChange={(event) => {
          setExportEnd(event.target.value);
          setExportPreview(null);
          setPreviewedExportInput(null);
        }} /></label>
        <button type="button" disabled={changed || !currentIds.length || exportBusy} onClick={() => void previewExport()}>
          {exportBusy ? (chinese ? "检查中…" : "Checking…") : (chinese ? "检查导出范围" : "Preview export")}
        </button>
      </div>
      {changed ? <div className="graph-message"><span>{chinese
        ? "请先保存资产选择，再导出该不可变修订。"
        : "Save the asset selection before exporting this immutable revision."}</span></div> : null}
      {activeExportPreview ? <div className="asset-export-preview">
        <Metric label={chinese ? "资产" : "Assets"} value={activeExportPreview.asset_count} />
        <Metric label={chinese ? "记录" : "Rows"} value={activeExportPreview.row_count.toLocaleString()} />
        <Metric label={chinese ? "日期" : "Dates"} value={`${activeExportPreview.start_date} → ${activeExportPreview.end_date}`} />
        <Metric label={chinese ? "估计大小" : "Estimated size"} value={formatBytes(activeExportPreview.estimated_bytes)} />
        <button type="button" disabled={exportBusy} onClick={() => void startExport()}>{chinese ? "开始后台导出" : "Start background export"}</button>
      </div> : null}
      {activeExportPreview?.warning_codes.length ? <div className="graph-message warning">
        <span>{chinese ? "数据提示" : "Data notices"}: {activeExportPreview.warning_codes.join(" · ")}</span>
      </div> : null}
      {exportJobId && exportStatus.data ? <div className="asset-export-status" role="status" aria-live="polite">
        <strong>{chinese ? `导出状态：${exportStatus.data.status}` : `Export status: ${exportStatus.data.status}`}</strong>
        <span>{exportStatus.data.processed_rows.toLocaleString()} / {exportStatus.data.total_rows.toLocaleString()} {chinese ? "行" : "rows"}</span>
        {exportStatus.data.local_delivery_path ? <code>{exportStatus.data.local_delivery_path}</code> : null}
        {exportStatus.data.download_url ? <a href={exportStatus.data.download_url}>{chinese ? "下载校验后的 ZIP" : "Download verified ZIP"}</a> : null}
        {["queued", "running"].includes(exportStatus.data.status) ? <button type="button" onClick={() => void api.cancelAssetDataExport(exportJobId).then(() => exportStatus.refetch())}>{chinese ? "取消导出" : "Cancel export"}</button> : null}
      </div> : null}
      {exportError || exportStatus.error ? <div className="graph-message error" role="alert"><span>{exportError ?? (exportStatus.error instanceof Error ? exportStatus.error.message : String(exportStatus.error))}</span></div> : null}
    </section>

    {selectedIds.length < 2 ? <div className="graph-message error" role="alert">
      <span>{chinese ? "至少选择两个同类资产，才能形成横截面研究 Universe。" : "Select at least two assets of one type to form a cross-sectional universe."}</span>
    </div> : null}
    {assets.isLoading ? <div className="graph-message"><span>{chinese ? "正在载入资产目录…" : "Loading asset catalog…"}</span></div> : null}
    {assets.error ? <div className="graph-message error"><span>{assets.error instanceof Error ? assets.error.message : String(assets.error)}</span><button type="button" onClick={() => void assets.refetch()}>{chinese ? "重试" : "Retry"}</button></div> : null}
    {!assets.isLoading && !assets.error ? <div className="universe-asset-grid">
      {visible.map((item) => {
        const group = candidateGroup(item);
        const eligible = isV022Candidate(item);
        const selected = selectedIds.includes(item.security_id);
        const incompatible = Boolean(selectedGroup && group && group !== selectedGroup);
        const disabled = !selected && (!eligible || incompatible);
        return <article className={`universe-asset-card${selected ? " selected" : ""}${disabled ? " disabled" : ""}`} key={item.security_id}>
          <label>
            <input type="checkbox" checked={selected} disabled={disabled} onChange={() => toggle(item)} />
            <span><strong>{item.symbol}</strong><small>{item.name}</small></span>
          </label>
          <div><code>{item.asset_key}</code><span>{item.instrument_type}</span></div>
          <p>{selected ? (chinese ? "已纳入当前候选集" : "Included in candidate universe")
            : incompatible ? (chinese ? `先清空当前${selectedLabel}候选集` : `Clear the current ${selectedLabel} universe first`)
            : eligible ? (chinese ? "属于当前共同实验数据集" : "Included in the shared experiment dataset")
            : item.selectable && item.canonical_data_available
              ? (chinese
                ? "存在独立行情，但不属于当前冻结的共同实验数据集"
                : "Individual history exists, but this asset is outside the frozen shared experiment dataset")
            : item.canonical_data_available ? (chinese ? "仅作参考或暂不支持该工具类型" : "Reference-only or unsupported instrument type")
            : (chinese ? "缺少已发布的规范行情" : "Published canonical market data unavailable")}</p>
        </article>;
      })}
      {!visible.length ? <div className="graph-message"><span>{chinese ? "没有符合当前筛选的资产。" : "No assets match the current filter."}</span></div> : null}
    </div> : null}
  </section>;
}

function assetContextMemberIds(context: Record<string, unknown> | undefined): string[] {
  if (!context || !Array.isArray(context.members)) return [];
  return context.members.flatMap((member) => {
    if (!member || typeof member !== "object") return [];
    const securityId = (member as Record<string, unknown>).security_id;
    return typeof securityId === "string" ? [securityId] : [];
  });
}

function candidateGroup(item: AssetCatalogItem): CandidateGroup | null {
  const instrument = item.instrument_type.toLocaleLowerCase().replaceAll("-", " ");
  if (instrument === "common stock" || instrument === "adr") return "stock";
  if (instrument.includes("etf") || instrument.includes("etp")) return "fund";
  return null;
}

function isV022Candidate(item: AssetCatalogItem): boolean {
  return Boolean(candidateGroup(item) && item.v022_candidate_selectable);
}

function graphPageCopy(view: GraphWorkspaceView, chinese: boolean) {
  if (chinese) {
    if (view === "context") return { title: "研究资产", subtitle: "选择参与最终信号排名和持仓构建的候选资产；基准、参考序列与防御资产保持独立。" };
    if (view === 1) return { title: "加工层 1", subtitle: "从合法原始输入出发，选择系统已经部署的第一层加工或透传结果。" };
    if (view === 2) return { title: "加工层 2", subtitle: "上游选择约束当前可用节点；也可以直接选择下游结果并由系统反向点亮祖先。" };
    if (view === 3) return { title: "加工层 3", subtitle: "本层的显式选择构成最终信号输入；只有这些信号会进入唯一聚合层。" };
    if (view === "aggregation") return { title: "聚合层", subtitle: "选择一个能够完整接受全部最终信号的聚合器，并明确选择其参数预设。" };
    return { title: "策略与防御", subtitle: "先在页面顶部检查并编译完整配置，再选择横截面策略与可选防御方案；编译不会自动启动实验。" };
  }
  if (view === "context") return { title: "Research assets", subtitle: "Choose candidate assets used for final-signal ranking and portfolio construction. Benchmarks, reference series and defensive assets remain independent." };
  if (view === 1) return { title: "Processing 1", subtitle: "Start from legal raw inputs and select the first published transformation or pass-through results." };
  if (view === 2) return { title: "Processing 2", subtitle: "Upstream choices constrain legal nodes, while downstream selection can resolve and illuminate its ancestors." };
  if (view === 3) return { title: "Processing 3", subtitle: "Explicit selections here are the final signals. Only this exact set enters the single aggregation layer." };
  if (view === "aggregation") return { title: "Aggregation", subtitle: "Choose one aggregator that accepts the complete final-signal set and select its explicit parameter presets." };
  return { title: "Strategy and defense", subtitle: "Review and compile the complete configuration at the top, then choose cross-sectional and optional defensive policies. Compilation does not start an experiment." };
}

function stageName(stageNo: StageNo, chinese: boolean) {
  if (!chinese) return stageNames[stageNo];
  return stageNo === 0 ? "原始输入" : `加工层 ${stageNo}`;
}

function StagePanel({ stage, pinned, catalog, totalCatalog, search, availability, loading, loadingMore, hasMore, error, pendingLabels, chinese, onSearch, onAvailability, onLoadMore, onRetry, onToggle, onBatchSelect, onInspect }: { stage: StageView; pinned: Family[]; catalog: Family[]; totalCatalog: number; search: string; availability: "all" | "ready" | "requires_ancestors" | "hard_incompatible"; loading: boolean; loadingMore: boolean; hasMore: boolean; error: string | null; pendingLabels: Set<string>; chinese: boolean; onSearch: (value: string) => void; onAvailability: (value: "all" | "ready" | "requires_ancestors" | "hard_incompatible") => void; onLoadMore: () => void; onRetry: () => void; onToggle: (item: Occurrence) => void; onBatchSelect: (items: Occurrence[]) => void; onInspect: (item: Occurrence) => void }) {
  const rawFamilies = stage.stage_no === 1
    ? uniqueFamilies([...stage.families, ...pinned, ...catalog].filter((family) => family.variants.some((item) => item.origin_stage === 0)))
    : [];
  const pinnedProcessing = pinned.filter((family) => family.variants.some((item) => item.origin_stage !== 0));
  const catalogProcessing = sortResearchFamilies(
    catalog.filter((family) => family.variants.some((item) => item.origin_stage !== 0)),
  );
  return <section className="graph-stage-panel"><header><div><p className="eyebrow">STAGE {stage.stage_no} / CONSISTENT CATALOG VIEW</p><h2>{stageName(stage.stage_no, chinese)}</h2></div><button type="button" onClick={() => downloadStageManifest({ ...stage, families: pinned })}>{chinese ? "导出本层血缘清单" : "Export lineage manifest"}</button></header>{rawFamilies.length ? <details className="raw-input-fold" open><summary><div><small>BOUND DATA INPUTS / STAGE 0 PROJECTIONS</small><strong>{chinese ? "自动数据输入与透传输出" : "Automatic data inputs and pass-through outputs"}</strong></div><span>{rawFamilies.length} {chinese ? "项" : "items"}</span></summary><p>{chinese ? "这些字段由所选研究资产的数据绑定自动提供，不是额外加工层。需要时可将原始输出作为加工层1的显式结果继续传递。" : "These fields are bound automatically from the selected research assets. They are not another processing layer; a raw output may be kept as an explicit Processing 1 result when needed."}</p><FamilySection title={chinese ? "已绑定字段" : "Bound fields"} families={rawFamilies} pendingLabels={pendingLabels} chinese={chinese} onToggle={onToggle} onBatchSelect={onBatchSelect} onInspect={onInspect} /></details> : null}{pinnedProcessing.length ? <FamilySection title={chinese ? "已选择与自动需要" : "Selected and required"} families={pinnedProcessing} pendingLabels={pendingLabels} chinese={chinese} onToggle={onToggle} onBatchSelect={onBatchSelect} onInspect={onInspect} /> : null}<div className="graph-catalog-toolbar"><label><span>{chinese ? "搜索目录" : "Search catalog"}</span><input type="search" value={search} placeholder={chinese ? "名称、Key 或经济含义" : "Name, key or research meaning"} onChange={(event) => onSearch(event.target.value)} /></label><label><span>{chinese ? "合法性" : "Availability"}</span><select value={availability} onChange={(event) => onAvailability(event.target.value as typeof availability)}><option value="all">{chinese ? "全部" : "All"}</option><option value="ready">Ready</option><option value="requires_ancestors">{chinese ? "需补齐祖先" : "Requires ancestors"}</option><option value="hard_incompatible">{chinese ? "不兼容" : "Incompatible"}</option></select></label><output aria-live="polite">{loading ? (chinese ? "正在加载…" : "Loading…") : `${catalogProcessing.length} / ${Math.max(0, totalCatalog - rawFamilies.length)}`}</output></div>{error ? <div className="graph-message error" role="alert"><span>{error}</span><button type="button" onClick={onRetry}>{chinese ? "重新载入" : "Retry"}</button></div> : null}{!loading && !error ? <FamilySection title={chinese ? "可选加工目录" : "Available transformations"} families={catalogProcessing} pendingLabels={pendingLabels} chinese={chinese} onToggle={onToggle} onBatchSelect={onBatchSelect} onInspect={onInspect} /> : null}{hasMore ? <button type="button" className="graph-load-more" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? (chinese ? "加载中…" : "Loading…") : (chinese ? "加载更多 Family" : "Load more families")}</button> : null}</section>;
}

function uniqueFamilies(families: Family[]): Family[] {
  return [...new Map(families.map((family) => [family.family_key, family])).values()];
}

const classicFamilyOrder = [
  "total_return",
  "lagged_return",
  "return_continuation",
  "realized_volatility",
  "low_volatility",
  "moving_average_ratio",
  "golden_cross_event",
  "death_cross_event",
  "rsi",
  "ppo_histogram",
  "return_skewness",
  "return_excess_kurtosis",
  "maximum_drawdown",
  "downside_deviation",
  "relative_dollar_volume",
  "amihud_illiquidity",
  "daily_price_impact",
] as const;

function sortResearchFamilies(families: Family[]): Family[] {
  const order = new Map<string, number>(
    classicFamilyOrder.map((key, index) => [key, index]),
  );
  return [...families].sort((left, right) => {
    const leftOrder = order.get(left.family_key) ?? classicFamilyOrder.length;
    const rightOrder = order.get(right.family_key) ?? classicFamilyOrder.length;
    return leftOrder - rightOrder || left.family_key.localeCompare(right.family_key);
  });
}

function FamilySection({ title, families, pendingLabels, chinese, onToggle, onBatchSelect, onInspect }: { title: string; families: Family[]; pendingLabels: Set<string>; chinese: boolean; onToggle: (item: Occurrence) => void; onBatchSelect: (items: Occurrence[]) => void; onInspect: (item: Occurrence) => void }) {
  const language = chinese ? "zh-CN" : "en";
  return <div className="graph-family-section"><h3>{title}<span>{families.length}</span></h3><div className="graph-family-grid">{families.map((family) => { const selectable = family.variants.filter((item) => !item.is_explicit && item.availability !== "hard_incompatible"); const translatedName = researchLabel(family.family_key, language); return <details className={`graph-family-card${family.pinned ? " pinned" : ""}`} key={family.family_key} open={family.pinned}><summary><div><small>FEATURE FAMILY</small><strong>{translatedName}</strong>{translatedName !== family.name ? <span className="graph-family-original">{family.name}</span> : null}<code>{family.family_key}</code></div><span>{family.explicit_count ? `${family.explicit_count} ${chinese ? "已选" : "selected"}` : family.required_count ? `${family.required_count} ${chinese ? "需要" : "required"}` : `${family.available_count} ${chinese ? "可用" : "available"}`}</span></summary><div className="graph-variant-list">{selectable.length > 1 ? <button type="button" className="graph-family-batch" onClick={() => onBatchSelect(selectable)}>{chinese ? `原子选择 ${selectable.length} 个 Variant` : `Select ${selectable.length} variants atomically`}</button> : null}{family.variants.map((item) => <VariantRow key={`${item.feature_key}@${item.stage_no}`} item={item} pending={pendingLabels.has(`${item.feature_key}@${item.stage_no}`)} chinese={chinese} onToggle={onToggle} onInspect={onInspect} />)}</div></details>; })}</div></div>;
}

function VariantRow({ item, pending, chinese, onToggle, onInspect }: { item: Occurrence; pending: boolean; chinese: boolean; onToggle: (item: Occurrence) => void; onInspect: (item: Occurrence) => void }) {
  const state = item.is_explicit && item.is_required ? "explicit-required" : item.is_explicit ? "explicit" : item.is_required ? "required" : item.availability;
  const status = item.is_explicit && item.is_required ? (chinese ? "已选择 · 被下游锁定" : "Selected · locked") : item.is_explicit ? (chinese ? "已选择" : "Selected") : item.is_required ? (chinese ? "下游需要" : "Required") : item.availability === "ready" ? (chinese ? "可直接选择" : "Ready") : item.availability === "requires_ancestors" ? (chinese ? `选择后补齐 ${item.select_effect.ancestor_count} 项` : `Adds ${item.select_effect.ancestor_count} ancestors`) : (chinese ? "当前聚合器不接受" : "Rejected by aggregator");
  const action = item.is_explicit ? (chinese ? "取消选择" : "Deselect") : item.is_required ? (chinese ? "保留为主动选择" : "Keep explicit") : item.stage_no === 3 ? (chinese ? "加入聚合输入" : "Add to aggregation") : (chinese ? "选择" : "Select");
  const language = chinese ? "zh-CN" : "en";
  const translatedName = researchLabel(item.family_key, language);
  const parameterEntries = Object.entries(item.parameters);
  const exactInputsAvailable = Array.isArray(item.input_feature_keys);
  const inputFeatureKeys = exactInputsAvailable ? item.input_feature_keys : [];
  const formulaIdentity = typeof item.formula_identity === "string" ? item.formula_identity : null;
  const semanticRole = typeof item.semantic_role === "string" ? item.semantic_role : null;
  const unit = typeof item.unit === "string" ? item.unit : null;
  const exactDefinitionPending = chinese ? "精确定义待服务重启" : "Exact definition available after service restart";
  return <article className={`graph-variant-row research-definition-card ${state}${pending ? " pending" : ""}`} aria-busy={pending}><div className="graph-variant-main"><span className="graph-state-badge">{pending ? (chinese ? "等待服务器确认" : "Pending") : status}</span><div className="research-definition-title"><strong>{translatedName}</strong>{translatedName !== item.name ? <span>{item.name}</span> : null}<code>{item.feature_key}</code></div><p>{researchRationale(item.research_hypothesis, language)}</p><div className="research-definition-grid"><section><span>{chinese ? "输入" : "Inputs"}</span><div>{!exactInputsAvailable ? <code>{exactDefinitionPending}</code> : inputFeatureKeys.length ? inputFeatureKeys.map((key) => <code key={key}>{key}</code>) : <code>{chinese ? "已发布数据源" : "Published source"}</code>}</div></section><section className="research-formula"><span>{chinese ? "公式 / 精确定义" : "Formula / exact definition"}</span>{formulaIdentity ? <FormulaDisplay factorKey={item.family_key} formula={formulaIdentity} /> : <code>{exactDefinitionPending}</code>}</section><section><span>{chinese ? "参数" : "Parameters"}</span><div>{parameterEntries.length ? parameterEntries.map(([key, value]) => <code key={key}>{key}={String(value)}</code>) : <code>{chinese ? "无可调参数" : "No tunable parameters"}</code>}</div></section><section><span>{chinese ? "输出" : "Output"}</span><div>{semanticRole ? <code>{researchLabel(semanticRole, language)}</code> : null}{unit ? <code>{unit}</code> : null}<code>{item.payload_contract_key}</code></div></section></div><small>{researchLabel(item.direction, language)} · {item.aggregation_readiness}</small>{item.locked_by.length ? <small className="lock-note">🔒 {chinese ? "依赖于" : "Required by"}: {item.locked_by.join(" · ")}</small> : null}</div><div className="graph-variant-actions"><button type="button" className="lineage-action" onClick={() => onInspect(item)}>{chinese ? "查看血缘" : "Lineage"}</button><button type="button" aria-pressed={item.is_explicit} disabled={pending || (item.availability === "hard_incompatible" && !item.is_explicit)} onClick={() => onToggle(item)}>{pending ? "…" : action}</button></div></article>;
}

const reasonCopy: Record<string, { zh: string; en: string }> = {
  frequency_unsupported: { zh: "当前研究频率不受支持", en: "The current research frequency is unsupported" },
  asset_context_required: { zh: "需要先冻结研究资产范围", en: "Freeze a research universe first" },
  asset_context_unsupported: { zh: "当前资产范围不适用于该防御方案", en: "The current universe is incompatible with this defense" },
  asset_registry_version_mismatch: { zh: "资产目录版本与防御篮子不一致", en: "The asset registry version differs from the defensive basket" },
  asset_context_instrument_type_unsupported: { zh: "当前资产类型不适用于该策略", en: "The current instrument type is unsupported by this strategy" },
  insufficient_eligible_assets: { zh: "符合条件的资产数量不足以构建该持仓数", en: "Too few eligible assets for this portfolio size" },
  aggregation_recipe_unavailable: { zh: "当前信号组合没有对应的已发布聚合方案", en: "No published aggregation recipe matches this signal set" },
  aggregation_recipe_ambiguous: { zh: "当前信号组合匹配到多个聚合方案，无法唯一冻结", en: "More than one aggregation recipe matches this signal set" },
  aggregation_native_requires_multiple_inputs: { zh: "原生分层等权至少需要两个信号", en: "Native hierarchical weighting requires at least two signals" },
  aggregation_taxonomy_entry_missing: { zh: "信号尚未发布研究维度分类", en: "The signal has no published research dimension" },
  aggregation_native_calibration_required: { zh: "事件或状态信号需先发布可比尺度转换", en: "Event or state scores require a published comparable-scale transform" },
  aggregation_native_scale_incompatible: { zh: "信号不是可直接等权的中心化排名尺度", en: "The signal is not on the comparable centered-rank scale" },
  aggregation_native_direction_incompatible: { zh: "信号方向尚未统一为数值越高越强", en: "The signal direction is not normalized to higher-is-better" },
};

function explainReason(reason: string, chinese: boolean): string {
  const copy = reasonCopy[reason];
  return copy ? copy[chinese ? "zh" : "en"] : researchLabel(reason, chinese ? "zh-CN" : "en");
}

function DefinitionValues({ values, empty }: { values: Record<string, unknown> | undefined; empty: string }) {
  const { i18n } = useTranslation();
  const entries = flattenDefinition(values ?? {});
  const localize = (value: string) => value
    .split(" · ")
    .map((part) => researchLabel(part, i18n.resolvedLanguage))
    .join(" · ");
  return <div>{entries.length
    ? entries.map(([key, value]) => <code key={key}>{key.split(".").map(localize).join(".")}={localize(value)}</code>)
    : <code>{empty}</code>}</div>;
}

function flattenDefinition(
  values: Record<string, unknown>,
  prefix = "",
): Array<[string, string]> {
  return Object.entries(values).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return flattenDefinition(value as Record<string, unknown>, path);
    }
    const display = Array.isArray(value)
      ? value.map(String).join(" · ")
      : typeof value === "boolean"
        ? (value ? "yes" : "no")
        : String(value);
    return [[path, display]];
  });
}

function percentage(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(0)}%` : String(value);
}

interface BlockerExplanation {
  title: string;
  detail: string;
  impact: string;
  action: string;
  target: StageTab;
}

const blockerCopy: Record<string, { zh: [string, string, string]; en: [string, string, string] }> = {
  stage3_input_required: {
    zh: ["还没有最终信号", "请在加工层 3 至少主动选择一个可进入聚合器的最终信号。", "没有最终信号时，聚合器和策略无法产生资产排名。"],
    en: ["No final signal selected", "Select at least one aggregation-ready final signal in Processing 3.", "Without a final signal, aggregation and strategy cannot produce an asset ranking."],
  },
  aggregation_required: {
    zh: ["还没有选择聚合方式", "请选择如何把全部最终信号合成为一个排序分数。", "研究图缺少从信号到策略输入的必要计算步骤。"],
    en: ["No aggregation method selected", "Choose how all final signals become one ranking score.", "The graph is missing the required signal-to-strategy calculation."],
  },
  input_count_rejected: {
    zh: ["信号数量不符合聚合器要求", "当前聚合器接受的输入数量范围与已选最终信号不一致。", "继续编译会产生定义不完整或不可执行的聚合实例。"],
    en: ["Signal count is outside the aggregator range", "The selected final-signal count does not meet this aggregator's bounds.", "Compiling would create an incomplete or non-executable aggregation instance."],
  },
  payload_contract_incompatible: {
    zh: ["部分输入不是可聚合的最终信号", "受影响项目的输出契约与当前聚合器不一致，请更换最终信号或聚合器。", "不兼容的数据不能被静默转换，否则会改变研究含义。"],
    en: ["Some inputs are not aggregation-ready signals", "Affected outputs do not match this aggregator's input contract; change the signals or aggregator.", "Incompatible data cannot be silently converted without changing research meaning."],
  },
  aggregation_parameter_preset_required: {
    zh: ["还需选择聚合方案", "请为该聚合器明确选择至少一个可用于当前信号集合的已发布方案。", "系统不会猜测维度、权重或投票规则；方案必须在编译前明确冻结。"],
    en: ["Choose an aggregation recipe", "Select at least one published recipe compatible with the current signal set.", "The system will not infer dimensions, weights, or voting rules; the recipe must be frozen before compile."],
  },
  aggregation_recipe_unavailable: {
    zh: ["聚合方案不适用于当前信号", "旧版分层与方向方案只支持精确迁移并冻结的信号集合，请更换方案或调整加工层 3 输入。", "继续运行会在工作进程中找不到唯一 Recipe，因此系统在编译前阻断。"],
    en: ["Aggregation recipe is unavailable for these signals", "Legacy hierarchical and directional recipes only accept exact migrated signal sets; choose another recipe or change Stage 3 inputs.", "The worker could not resolve a unique recipe, so the configuration is blocked before compile."],
  },
  aggregation_recipe_ambiguous: {
    zh: ["聚合方案身份不唯一", "当前信号集合匹配到多个冻结 Recipe，需要先修复 Catalog 身份。", "不唯一的权重定义无法可靠回放。"],
    en: ["Aggregation recipe identity is ambiguous", "The current signal set matches multiple frozen recipes; repair the Catalog identity first.", "Ambiguous weights cannot be replayed reliably."],
  },
  aggregation_native_requires_multiple_inputs: {
    zh: ["原生分层等权至少需要两个信号", "请在加工层 3 选择至少两个可比的最终信号。", "单信号无需分层，使用单信号直通即可。"],
    en: ["Native hierarchy needs at least two signals", "Select at least two comparable Stage 3 signals.", "Use single-signal identity when only one signal is selected."],
  },
  aggregation_taxonomy_entry_missing: {
    zh: ["信号缺少研究维度分类", "该信号还没有发布可审计的研究维度，不能由运行时按名称猜测。", "请改选已有 taxonomy 的信号或先更新 Catalog。"],
    en: ["Signal taxonomy is missing", "This signal has no published research dimension and runtime inference is forbidden.", "Choose a classified signal or publish a new Catalog taxonomy."],
  },
  aggregation_native_calibration_required: {
    zh: ["信号尺度尚不可直接等权", "事件分数或状态分数必须先经过已发布的排名/校准变换。", "直接与中心化排名平均会改变金融含义。"],
    en: ["Signal scale needs calibration", "Event and state scores require a published ranking or calibration transform.", "A raw average with centered ranks would change the research meaning."],
  },
  aggregation_native_scale_incompatible: {
    zh: ["信号尺度不兼容", "原生分层等权首版仅接受 centered_rank 输出。", "系统不会静默标准化或转换。"],
    en: ["Signal scale is incompatible", "The first native hierarchical release accepts centered_rank outputs only.", "The system will not standardize or transform silently."],
  },
  aggregation_native_direction_incompatible: {
    zh: ["信号方向不兼容", "原生分层等权要求所有输入已统一为数值越高、信号越强。", "方向变换必须先作为 Feature Version 发布。"],
    en: ["Signal direction is incompatible", "Native hierarchical weighting requires every input to use higher-is-better semantics.", "Direction transforms must be published as Feature Versions first."],
  },
  strategy_required: {
    zh: ["还没有选择策略", "请选择一个把资产排序转换为持仓目标的横截面策略。", "缺少策略时，最终信号不能形成组合。"],
    en: ["No strategy selected", "Choose a cross-sectional strategy that converts ranks into portfolio targets.", "Without a strategy, final signals cannot form a portfolio."],
  },
  strategy_parameter_preset_required: {
    zh: ["策略缺少持仓参数方案", "请明确选择 Top-K 持仓数量等已发布策略参数。", "没有明确持仓规则时，实验分支身份不完整。"],
    en: ["Strategy preset required", "Select published strategy parameters such as the Top-K portfolio size.", "Without an exact holding rule, the experiment branch identity is incomplete."],
  },
  defense_selection_required: {
    zh: ["尚未决定是否启用防御", "请选择“不启用防御”或一个明确的防御方案。", "风险与防御预算必须被明确冻结，不能依赖默认猜测。"],
    en: ["Defense choice required", "Choose either No defense or an explicit defensive package.", "Risk and defense budgets must be frozen explicitly rather than inferred."],
  },
  frequency_unsupported: {
    zh: ["当前频率不受支持", "所选策略或防御方案不支持当前周频/月频设置。", "按不支持的频率运行会改变调仓与输入时间语义。"],
    en: ["Current frequency is unsupported", "The selected strategy or defense does not support the current weekly/monthly setting.", "Running at an unsupported frequency would change schedule and input-time semantics."],
  },
  asset_context_required: {
    zh: ["需要先冻结研究资产", "策略需要明确的资产成员、类型和数据覆盖范围。", "没有冻结资产范围就无法验证持仓数量和数据完整性。"],
    en: ["A frozen research universe is required", "The strategy needs exact members, instrument types and data coverage.", "Portfolio size and data completeness cannot be validated without a frozen universe."],
  },
  asset_context_instrument_type_unsupported: {
    zh: ["资产类型与策略不匹配", "当前资产范围包含该策略不接受的工具类型。", "继续运行可能产生没有定义的排名或持仓行为。"],
    en: ["Universe instruments do not match the strategy", "The current universe contains instrument types unsupported by this strategy.", "Continuing could create undefined ranking or holding behavior."],
  },
  insufficient_eligible_assets: {
    zh: ["可用资产数量不足", "当前可执行资产少于所选 Top-K 持仓数量，请缩小 K 或扩大资产范围。", "策略无法构建要求数量的合法持仓。"],
    en: ["Too few eligible assets", "There are fewer executable assets than the selected Top-K; reduce K or expand the universe.", "The strategy cannot construct the required number of valid positions."],
  },
  asset_context_unsupported: {
    zh: ["防御方案不支持当前资产范围", "请选择兼容的防御方案，或暂时使用“不启用防御”。", "防御篮子与风险资产上下文的兼容性尚未得到证明。"],
    en: ["Defense does not support this universe", "Choose a compatible defense or use No defense for now.", "Compatibility between the defensive basket and risk universe is not proven."],
  },
  asset_registry_version_mismatch: {
    zh: ["防御篮子与资产目录版本不一致", "请更新资产范围或选择匹配当前目录版本的防御方案。", "版本漂移会破坏防御资产身份与回放一致性。"],
    en: ["Defensive basket uses a different asset registry", "Update the universe or choose a defense pinned to the current registry.", "Registry drift would break defensive identity and replay consistency."],
  },
};

function blockerTarget(blocker: GraphBlocker): StageTab {
  if (blocker.reason_codes.some((reason) => reason.startsWith("asset_"))) return 0;
  if (blocker.layer === "stage3") return 3;
  if (blocker.layer === "aggregation") return "aggregation";
  return "strategy";
}

function blockerAction(target: StageTab, chinese: boolean): string {
  if (target === 0) return chinese ? "检查资产范围" : "Review universe";
  if (target === 3) return chinese ? "前往加工层 3" : "Go to Processing 3";
  if (target === "aggregation") return chinese ? "调整聚合方式" : "Edit aggregation";
  return chinese ? "调整策略与防御" : "Edit strategy and defense";
}

function explainBlocker(blocker: GraphBlocker, chinese: boolean): BlockerExplanation {
  const target = blockerTarget(blocker);
  const reason = blocker.reason_codes[0] ?? "configuration_invalid";
  const copy = blockerCopy[reason]?.[chinese ? "zh" : "en"];
  return {
    title: copy?.[0] ?? (chinese ? "配置仍需调整" : "Configuration needs attention"),
    detail: copy?.[1] ?? blocker.reason_codes.map((item) => explainReason(item, chinese)).join(" · "),
    impact: copy?.[2] ?? (chinese ? "为保证研究身份可重放，系统不会猜测或自动修正该配置。" : "To preserve replayable research identity, the system will not guess or silently repair this configuration."),
    action: blockerAction(target, chinese),
    target,
  };
}

function aggregationSummary(key: string, chinese: boolean): string {
  const summaries: Record<string, { zh: string; en: string }> = {
    single_signal_identity: { zh: "保留单个最终信号，不改变其数值或方向。", en: "Pass one final signal through without changing its value or direction." },
    flat_equal_weight_mean: { zh: "将全部显式信号按相同权重求平均，得到一个综合排序分数。", en: "Average every explicit signal with equal weight into one composite ranking score." },
    hierarchical_weighted_mean: { zh: "先在研究维度内聚合，再按已发布的维度权重合并，避免信号数量多的维度天然占优。", en: "Aggregate within research dimensions, then combine published dimension weights so large dimensions do not dominate." },
    directional_weighted_vote: { zh: "把信号转换为方向投票，再按已发布权重合并，强调方向一致性。", en: "Convert signals into directional votes and combine them with published weights." },
    ols_cross_sectional_regression: { zh: "用扩展窗口 OLS 学习全部已选信号与未来横截面排名的线性关系，并只发布严格样本外预测。", en: "Fit expanding-window OLS on every selected signal and publish strict out-of-sample cross-sectional predictions." },
    ridge_cross_sectional_regression: { zh: "用 L2 收缩稳定相关信号的线性系数，并只发布严格样本外预测。", en: "Use L2 shrinkage to stabilize correlated signal coefficients and publish strict out-of-sample predictions." },
    random_forest_cross_sectional_regression: { zh: "用多棵受控回归树学习非线性关系与信号交互，再平均严格样本外预测。", en: "Use bounded regression trees to learn nonlinearities and interactions, then average strict out-of-sample predictions." },
    lightgbm_cross_sectional_regression: { zh: "用受控的 LightGBM 提升树学习非线性横截面关系，训练与预测严格按时间走步。", en: "Learn nonlinear cross-sectional relationships with bounded LightGBM under strict walk-forward training." },
    xgboost_cross_sectional_regression: { zh: "用带正则化的 XGBoost 提升树学习非线性横截面关系，训练与预测严格按时间走步。", en: "Learn regularized nonlinear cross-sectional relationships with XGBoost under strict walk-forward training." },
  };
  const copy = summaries[key];
  return copy ? copy[chinese ? "zh" : "en"] : key;
}

function AggregationPanel({ data, selected, onChange, onPresetChange, onTargetChange, onTrainingChange, chinese }: { data: GraphDerivedView; selected: string[]; onChange: (next: string[]) => void; onPresetChange: (familyKey: string, presets: string[]) => void; onTargetChange: (familyKey: string, targets: string[]) => void; onTrainingChange: (familyKey: string, presets: string[]) => void; chinese: boolean }) {
  const aggregations = data.aggregations as ExplainedAggregationOption[];
  return <section className="graph-stage-panel">
    <header><div><p className="eyebrow">AGGREGATION / ALL INPUTS CONSUMED</p><h2>{chinese ? "一个最终聚合层" : "One final aggregation layer"}</h2><p>{chinese ? "每个聚合器都会消费加工层 3 的全部显式信号；这里决定如何把它们变成最终排序分数。" : "Each aggregator consumes every explicit Stage 3 signal and defines how they become the final ranking score."}</p></div></header>
    <div className="aggregation-input-bar"><span>{chinese ? "加工层 3 显式输入" : "Explicit Stage 3 inputs"}</span>{data.aggregation_inputs.length ? data.aggregation_inputs.map((key) => <code key={key}>{researchLabel(key, chinese ? "zh-CN" : "en")}</code>) : <strong>{chinese ? "尚未选择" : "None selected"}</strong>}</div>
    <div className="graph-family-grid">{aggregations.map((item) => {
      const definitions = item.parameter_preset_definitions ?? item.parameter_presets.map((preset) => ({ preset_key: preset, name: researchLabel(preset, chinese ? "zh-CN" : "en"), description: "", version_number: 1, semantics: {}, selected: item.selected_parameter_presets.includes(preset), selectable: true, reason_codes: [] }));
      return <article className={`aggregation-card research-rule-card${item.selected ? " selected" : ""}`} key={item.family_key}>
        <small>AGGREGATION FAMILY</small><h3>{researchLabel(item.family_key, chinese ? "zh-CN" : "en")}</h3>{item.name !== researchLabel(item.family_key, chinese ? "zh-CN" : "en") ? <span>{item.name}</span> : null}<code>{item.family_key}</code>
        <p>{aggregationSummary(item.family_key, chinese)}</p>
        <div className="research-definition-grid">
          <section className="research-formula"><span>{chinese ? "计算规则" : "Calculation"}</span>{item.algorithm_identity ? <FormulaDisplay factorKey={item.family_key} formula={item.algorithm_identity} /> : <code>{chinese ? "服务重启后显示精确定义" : "Exact definition available after service restart"}</code>}</section>
          <section><span>{chinese ? "输入约束" : "Input contract"}</span><div><code>{item.minimum_inputs}–{item.maximum_inputs}</code><code>{item.input_payload_contract_key ?? "final_signal_numeric"}</code></div><DefinitionValues values={item.input_policy} empty={chinese ? "消费全部显式输入" : "Consume all explicit inputs"} /></section>
          <section><span>{chinese ? "输出" : "Output"}</span><div><code>{item.output_payload_contract_key ?? "final_signal_numeric"}</code><code>{researchLabel(String(item.output_semantics?.direction ?? "higher_is_better"), chinese ? "zh-CN" : "en")}</code></div></section>
          <section><span>{chinese ? "缺失与并列" : "Missing values and ties"}</span><DefinitionValues values={{ ...(item.missing_policy ?? {}), ...(item.tie_policy ?? {}) }} empty={chinese ? "保留已发布数值" : "Preserve published values"} /></section>
        </div>
        <p>{item.accepted_input_count} / {data.aggregation_inputs.length} {chinese ? "项输入满足基础数值契约；具体方案仍需单独验证" : "inputs satisfy the base numeric contract; each recipe is validated separately"}</p>
        <button type="button" aria-pressed={item.selected} onClick={() => onChange(selected.includes(item.family_key) ? selected.filter((key) => key !== item.family_key) : [...selected, item.family_key])}>{item.selected ? (chinese ? "取消聚合器" : "Deselect") : (chinese ? "选择聚合器" : "Select")}</button>
        {item.selected && definitions.length ? <fieldset className="research-preset-fieldset"><legend>{chinese ? "选择聚合方案（必选）" : "Choose aggregation recipe (required)"}</legend>{definitions.map((preset) => {
          const selectedPreset = item.selected_parameter_presets.includes(preset.preset_key);
          const disabled = !preset.selectable && !selectedPreset;
          const reasonsId = `aggregation-${item.family_key}-${preset.preset_key}-reasons`;
          return <label className={`research-preset-option${disabled ? " disabled" : ""}`} key={preset.preset_key}><input type="checkbox" aria-label={preset.preset_key} aria-describedby={preset.reason_codes.length ? reasonsId : undefined} checked={selectedPreset} disabled={disabled} onChange={() => onPresetChange(item.family_key, selectedPreset ? item.selected_parameter_presets.filter((key) => key !== preset.preset_key) : [...item.selected_parameter_presets, preset.preset_key])} /><span><strong>{researchLabel(preset.preset_key, chinese ? "zh-CN" : "en")}</strong><code>{preset.preset_key}@v{preset.version_number}</code>{preset.description ? <small>{researchDescription(preset.preset_key, preset.description, chinese ? "zh-CN" : "en")}</small> : null}<DefinitionValues values={preset.semantics} empty={chinese ? "无额外参数" : "No extra parameters"} />{preset.reason_codes.length ? <small className="compatibility-note" id={reasonsId}>{preset.reason_codes.map((reason) => explainReason(reason, chinese)).join(" · ")}</small> : null}</span></label>;
        })}</fieldset> : <p>{chinese ? "该聚合器没有额外方案轴" : "This aggregator has no additional recipe axis"}</p>}
        {item.selected && item.execution_mode === "supervised" ? <>
          <fieldset className="research-preset-fieldset"><legend>{chinese ? "预测目标（可多选）" : "Prediction targets (multiple allowed)"}</legend>{(item.targets ?? []).map((target) => { const targetSelected = (item.selected_targets ?? []).includes(target.key); return <label className="research-preset-option" key={target.key}><input type="checkbox" aria-label={target.key} checked={targetSelected} onChange={() => onTargetChange(item.family_key, targetSelected ? (item.selected_targets ?? []).filter((key) => key !== target.key) : [...(item.selected_targets ?? []), target.key])} /><span><strong>{researchLabel(target.key, chinese ? "zh-CN" : "en")}</strong><code>{target.key}@v{target.version_number}</code><small>{researchDescription(target.key, target.description, chinese ? "zh-CN" : "en")}</small><DefinitionValues values={target.semantics} empty={chinese ? "固定目标定义" : "Frozen target definition"} /></span></label>; })}</fieldset>
          <fieldset className="research-preset-fieldset"><legend>{chinese ? "训练方案（可多选）" : "Training recipes (multiple allowed)"}</legend>{(item.training_presets ?? []).map((preset) => { const presetSelected = (item.selected_training_presets ?? []).includes(preset.key); return <label className="research-preset-option" key={preset.key}><input type="checkbox" aria-label={preset.key} checked={presetSelected} onChange={() => onTrainingChange(item.family_key, presetSelected ? (item.selected_training_presets ?? []).filter((key) => key !== preset.key) : [...(item.selected_training_presets ?? []), preset.key])} /><span><strong>{researchLabel(preset.key, chinese ? "zh-CN" : "en")}</strong><code>{preset.key}@v{preset.version_number}</code><small>{researchDescription(preset.key, preset.description, chinese ? "zh-CN" : "en")}</small><DefinitionValues values={preset.semantics} empty={chinese ? "固定训练定义" : "Frozen training definition"} /></span></label>; })}</fieldset>
          <p>{chinese ? `内部集成成员：${item.internal_member_count ?? 0}；同一目标内方案等权，不同目标组再次等权。` : `Internal ensemble members: ${item.internal_member_count ?? 0}; recipes are equal-weighted within each target, then target groups are equal-weighted.`}</p>
        </> : null}
      </article>;
    })}</div>
  </section>;
}

function normalizeStrategies(data: GraphDerivedView): StrategyOption[] {
  return (data.strategies as TransitionalStrategyOption[]).map((item) => ({
    ...item,
    parameter_presets: [...(item.parameter_presets ?? [])].sort((left, right) => (
      Number(right.selected) - Number(left.selected)
      || left.preset_key.localeCompare(right.preset_key)
    )),
  }));
}

function strategyFamilies(data: GraphDerivedView): StrategyFamily[] {
  const grouped = new Map<string, StrategyFamily>();
  for (const strategy of normalizeStrategies(data)) {
    const family = grouped.get(strategy.family_key) ?? {
      familyKey: strategy.family_key,
      name: strategy.name,
      selected: false,
      variants: [],
    };
    family.selected = family.selected
      || strategy.selected
      || strategy.parameter_presets.some((preset) => preset.selected);
    family.variants.push(strategy);
    grouped.set(strategy.family_key, family);
  }
  return [...grouped.values()]
    .map((family) => ({
      ...family,
      variants: family.variants.sort((left, right) => (
        Number(
          right.selected || right.parameter_presets.some((preset) => preset.selected),
        )
        - Number(
          left.selected || left.parameter_presets.some((preset) => preset.selected),
        )
        || left.variant_key.localeCompare(right.variant_key)
      )),
    }))
    .sort((left, right) => (
      Number(right.selected) - Number(left.selected)
      || left.familyKey.localeCompare(right.familyKey)
    ));
}

function StrategyDefensePanel({
  data,
  chinese,
  pendingLabels,
  onStrategyPresets,
  onSelectAllStrategies,
  onClearStrategies,
  onDefense,
  onClearDefenses,
}: {
  data: GraphDerivedView;
  chinese: boolean;
  pendingLabels: Set<string>;
  onStrategyPresets: (key: string, presetKeys: string[]) => void;
  onSelectAllStrategies: () => void;
  onClearStrategies: () => void;
  onDefense: (key: string, selected: boolean) => void;
  onClearDefenses: () => void;
}) {
  const families = strategyFamilies(data);
  return <section className="graph-stage-panel">
    <header>
      <div>
        <p className="eyebrow">STRATEGY / DEFENSE BRANCH AXES</p>
        <h2>{chinese ? "横截面策略与防御配置" : "Cross-sectional strategy and defense"}</h2>
      </div>
      <strong>{data.summary.strategy_branch_count} {chinese ? "个分支" : "branches"}</strong>
    </header>
    <div className="strategy-defense-grid">
      <div>
        <div className="graph-variant-actions">
          <h3>{chinese ? "策略" : "Strategy"}</h3>
          <button type="button" onClick={onSelectAllStrategies}>{chinese ? "全选合法策略" : "Select all compatible"}</button>
          <button type="button" onClick={onClearStrategies}>{chinese ? "清空策略" : "Clear strategies"}</button>
        </div>
        <div className="strategy-family-list">
          {families.map((family) => <article
            className={`strategy-family-card${family.selected ? " selected" : ""}`}
            key={family.familyKey}
          >
            <header>
              <small>STRATEGY FAMILY</small>
              <h4>{researchLabel(family.familyKey, chinese ? "zh-CN" : "en")}</h4>
              <code>{family.familyKey}</code>
            </header>
            <div className="strategy-variant-list">
              {family.variants.map((variant) => {
                const selectedPresetKeys = variant.parameter_presets
                  .filter((preset) => preset.selected)
                  .map((preset) => preset.preset_key);
                const pending = pendingLabels.has(`strategy:${variant.variant_key}`);
                return <fieldset
                  className={`strategy-variant-card${variant.selected ? " selected" : ""}`}
                  aria-busy={pending}
                  key={variant.variant_key}
                >
                  <legend>
                    <strong>{researchLabel(variant.variant_key, chinese ? "zh-CN" : "en")}</strong>
                    <code>{variant.variant_key}</code>
                  </legend>
                  {!chinese && variant.name !== researchLabel(variant.variant_key, "en") ? <span>{variant.name}</span> : null}
                  <p>{variant.research_hypothesis
                    ? researchRationale(variant.research_hypothesis, chinese ? "zh-CN" : "en")
                    : (chinese ? "按综合信号在资产之间进行横截面排名，并等权持有排名靠前的资产。" : "Rank assets cross-sectionally by the composite signal and equal-weight the leaders.")}</p>
                  <div className="research-definition-grid strategy-rule-grid">
                    <section><span>{chinese ? "选择规则" : "Selection rule"}</span><DefinitionValues values={variant.selection_semantics} empty={chinese ? "按最终分数降序排名" : "Rank by final score descending"} /></section>
                    <section><span>{chinese ? "策略参数" : "Strategy parameters"}</span><DefinitionValues values={variant.parameters} empty={chinese ? "无固定参数" : "No fixed parameters"} /></section>
                    <section><span>{chinese ? "调仓计划" : "Schedule"}</span><DefinitionValues values={variant.schedule_policy} empty={variant.supported_frequencies.map((value) => researchLabel(value, chinese ? "zh-CN" : "en")).join(" · ")} /></section>
                    <section><span>{chinese ? "执行约束" : "Execution"}</span><DefinitionValues values={variant.execution_policy} empty={chinese ? "多头等权" : "Long-only equal weight"} /></section>
                  </div>
                  {variant.reason_codes.map((reason) => <small className="research-reason" key={reason}>{explainReason(reason, chinese)}</small>)}
                  <div className="strategy-preset-list">
                    {variant.parameter_presets.map((preset) => {
                      const reasonsId = `strategy-${variant.variant_key}-${preset.preset_key}-reasons`;
                      const unavailable = !preset.selectable && !preset.selected;
                      const invalidSelected = preset.selected && !preset.selectable;
                      return <label
                        className={`strategy-preset-option${preset.selected ? " selected" : ""}${unavailable ? " unavailable" : ""}${invalidSelected ? " invalid-selected" : ""}`}
                        key={preset.preset_key}
                      >
                        <input
                          type="checkbox"
                          aria-label={`${variant.variant_key} / ${preset.name}`}
                          aria-describedby={preset.reason_codes.length ? reasonsId : undefined}
                          checked={preset.selected}
                          disabled={pending || unavailable}
                          onChange={() => onStrategyPresets(
                            variant.variant_key,
                            (preset.selected
                              ? selectedPresetKeys.filter((key) => key !== preset.preset_key)
                              : [...selectedPresetKeys, preset.preset_key]
                            ).sort(),
                          )}
                        />
                        <span className="strategy-preset-copy">
                          <strong>{researchLabel(preset.preset_key, chinese ? "zh-CN" : "en")}</strong>
                          <code>{preset.preset_key}@v{preset.version_number}</code>
                          {!chinese && preset.description ? <small>{preset.description}</small> : null}
                          <DefinitionValues values={preset.parameters} empty={chinese ? "无额外参数" : "No extra parameters"} />
                          {preset.reason_codes.length ? <small
                            className="strategy-preset-reasons"
                            id={reasonsId}
                          >{preset.reason_codes.map((reason) => explainReason(reason, chinese)).join(" · ")}</small> : null}
                        </span>
                      </label>;
                    })}
                  </div>
                </fieldset>;
              })}
            </div>
          </article>)}
        </div>
      </div>
      <div>
        <div className="graph-variant-actions">
          <h3>{chinese ? "防御" : "Defense"}</h3>
          <button type="button" onClick={onClearDefenses}>{chinese ? "使用不防御" : "Use no defense"}</button>
        </div>
        <div className="defense-package-list">
          {(data.defenses as ExplainedDefenseOption[]).map((item) => {
            const reasons = item.reason_codes ?? [];
            const unavailable = !item.compatible && !item.selected;
            const invalidSelected = item.selected && !item.compatible;
            const pending = pendingLabels.has(`defense:${item.variant_key}`);
            const reasonsId = `defense-${item.variant_key}-reasons`;
            return <article
              aria-busy={pending}
              className={`aggregation-card defense-package-card${item.selected ? " selected" : ""}${unavailable ? " unavailable" : ""}${invalidSelected ? " invalid-selected" : ""}`}
              key={item.variant_key}
            >
              <header>
                <small>{item.composed ? "DEFENSE PACKAGE" : "DEFENSE"} · {item.family_key}</small>
                <h3>{researchLabel(item.variant_key, chinese ? "zh-CN" : "en")}</h3>
                <code>{item.variant_key}{item.version_number ? `@v${item.version_number}` : ""}</code>
                {item.research_status
                  ? <span className="defense-status">{researchLabel(item.research_status, chinese ? "zh-CN" : "en")}</span>
                  : null}
              </header>
              <p>{item.research_hypothesis
                ? researchRationale(item.research_hypothesis, chinese ? "zh-CN" : "en")
                : (item.variant_key === "none"
                  ? (chinese ? "全部资金保持在风险资产组合中，不创建防御资产袖套。" : "Keep the full portfolio in the risk sleeve; no defensive sleeve is created.")
                  : researchLabel(item.variant_key, chinese ? "zh-CN" : "en"))}</p>
              <div className="research-definition-grid defense-rule-grid">
                <section><span>{chinese ? "风险预算" : "Risk budget"}</span><DefinitionValues values={item.allocation_semantics} empty={item.variant_key === "none" ? (chinese ? "风险 100% · 防御 0%" : "Risk 100% · defense 0%") : (chinese ? "由择时规则决定" : "Set by timing policy")} /></section>
                <section><span>{chinese ? "输入要求" : "Inputs"}</span><DefinitionValues values={item.input_policy} empty={item.variant_key === "none" ? (chinese ? "不需要防御输入" : "No defense inputs") : (chinese ? "使用冻结的防御数据" : "Frozen defense inputs")} /></section>
              </div>
              {item.timing_policy ? <section className="defense-policy-card">
                <small>{chinese ? "择时政策" : "Timing policy"}</small>
                <strong>{researchLabel(item.timing_policy.variant_key, chinese ? "zh-CN" : "en")}</strong>
                <code>{item.timing_policy.variant_key}@v{item.timing_policy.version_number}</code>
                {item.timing_policy.research_hypothesis ? <p>{researchRationale(item.timing_policy.research_hypothesis, chinese ? "zh-CN" : "en")}</p> : null}
                <span>{item.timing_policy.supported_frequencies.map((value) => researchLabel(value, chinese ? "zh-CN" : "en")).join(" · ")}</span>
                {item.timing_policy.formula_identity ? <FormulaDisplay factorKey={item.timing_policy.variant_key} formula={item.timing_policy.formula_identity} /> : null}
                <DefinitionValues values={item.timing_policy.rule} empty={chinese ? "固定预算" : "Fixed budget"} />
              </section> : null}
              {item.allocation_policy ? <section className="defense-policy-card">
                <small>{chinese ? "防守配置" : "Defensive allocation"}</small>
                <strong>{researchLabel(item.allocation_policy.variant_key, chinese ? "zh-CN" : "en")}</strong>
                <code>{item.allocation_policy.variant_key}@v{item.allocation_policy.version_number}</code>
                {item.allocation_policy.research_hypothesis ? <p>{researchRationale(item.allocation_policy.research_hypothesis, chinese ? "zh-CN" : "en")}</p> : null}
                {item.allocation_policy.formula_identity ? <FormulaDisplay factorKey={item.allocation_policy.variant_key} formula={item.allocation_policy.formula_identity} /> : null}
                <span>{item.allocation_policy.asset_set_key}</span>
                <span>{item.allocation_policy.formal_eligible
                  ? (chinese ? "可用于正式研究" : "Formal eligible")
                  : (chinese ? "仅探索 / 对齐研究" : "Exploratory / parity only")}</span>
                <div className="defense-member-list">
                  {item.allocation_policy.members.map((member) => <code key={member.ordinal}>
                    {researchLabel(member.asset_key, chinese ? "zh-CN" : "en")} · {percentage(member.sleeve_weight)}
                  </code>)}
                </div>
              </section> : null}
              {reasons.length ? <small className="defense-reasons" id={reasonsId}>
                {reasons.map((reason) => explainReason(reason, chinese)).join(" · ")}
              </small> : null}
              <button
                type="button"
                aria-describedby={reasons.length ? reasonsId : undefined}
                aria-pressed={item.selected}
                disabled={pending || unavailable}
                onClick={() => onDefense(item.variant_key, item.selected)}
              >{item.selected
                  ? (chinese ? "取消防御分支" : "Deselect defense")
                  : (chinese ? "加入防御分支" : "Select defense")}</button>
            </article>;
          })}
        </div>
      </div>
    </div>
  </section>;
}

function ReviewPanel({ data, chinese, onNavigate, onCompile, onProceed, disabled, compiledFingerprint }: { data: GraphDerivedView; chinese: boolean; onNavigate: (tab: StageTab) => void; onCompile: () => void; onProceed: () => void; disabled: boolean; compiledFingerprint: string | null }) {
  const selectedAggregations = data.aggregations.filter((item) => item.selected);
  const selectedStrategyPresets = normalizeStrategies(data).flatMap((strategy) => (
    strategy.parameter_presets
      .filter((preset) => preset.selected)
      .map((preset) => ({ strategy, preset }))
  ));
  return <section className="graph-stage-panel graph-review" id="configuration-review">
    <header>
      <div>
        <p className="eyebrow">CONFIGURATION REVIEW / EXACT DERIVED STATE</p>
        <h2>{chinese ? "配置检查与编译" : "Configuration review and compile"}</h2>
        <p>{chinese
          ? "这是策略工作流的提交前检查区，不是加工层；编译整个研究图，但尚不启动实验。"
          : "This is the pre-submit validation area in the strategy workflow, not a processing layer. It compiles the whole research graph but does not start an experiment."}</p>
      </div>
      <code>{data.derived_state_fingerprint.slice(0, 16)}</code>
    </header>
    <div className="review-grid">
      <article>
        <span>{chinese ? "最终信号" : "Final signals"}</span>
        <strong>{data.aggregation_inputs.length}</strong>
        {data.aggregation_inputs.map((key) => <code key={key}>{researchLabel(key, chinese ? "zh-CN" : "en")}</code>)}
        <button type="button" onClick={() => onNavigate(3)}>{chinese ? "返回加工层 3" : "Back to Stage 3"}</button>
      </article>
      <article>
        <span>{chinese ? "聚合器 / Preset" : "Aggregators / presets"}</span>
        <strong>{selectedAggregations.length}</strong>
        {selectedAggregations.map((item) => <code key={item.family_key}>{researchLabel(item.family_key, chinese ? "zh-CN" : "en")}: {item.selected_parameter_presets.map((key) => researchLabel(key, chinese ? "zh-CN" : "en")).join(", ") || (chinese ? "无参数轴" : "no parameter axis")}</code>)}
        <button type="button" onClick={() => onNavigate("aggregation")}>{chinese ? "调整聚合层" : "Edit aggregation"}</button>
      </article>
      <article>
        <span>Strategy / Defense</span>
        <strong>{selectedStrategyPresets.length}</strong>
        {selectedStrategyPresets.map(({ strategy, preset }) => <div className="review-definition" key={`${strategy.variant_key}/${preset.preset_key}`}>
          <strong>{researchLabel(strategy.variant_key, chinese ? "zh-CN" : "en")} · {researchLabel(preset.preset_key, chinese ? "zh-CN" : "en")}</strong>
          <code
          key={`${strategy.variant_key}/${preset.preset_key}`}
        >
          {strategy.variant_key} · {preset.preset_key}@v{preset.version_number}
        </code><DefinitionValues values={preset.parameters} empty={chinese ? "无额外参数" : "No extra parameters"} /></div>)}
        {data.defenses.filter((item) => item.selected).map((item) => <div className="review-definition" key={item.variant_key}>
          <strong>{researchLabel(item.variant_key, chinese ? "zh-CN" : "en")}</strong>
          <code>{item.composed && item.timing_policy && item.allocation_policy
            ? `${item.variant_key}@v${item.version_number} · ${item.timing_policy.variant_key}@v${item.timing_policy.version_number} · ${item.allocation_policy.variant_key}@v${item.allocation_policy.version_number}`
            : item.variant_key}</code>
        </div>)}
      </article>
      <article>
        <span>{chinese ? "预计实验分支 / Cell" : "Expected branches / cells"}</span>
        <strong>{data.summary.strategy_branch_count} / {data.summary.backtest_cell_count}</strong>
        <small>{data.frequency} · {data.catalog_release.catalog_version}</small>
      </article>
      <article>
        <span>{chinese ? "资源准入" : "Resource admission"}</span>
        <strong>{data.resources.state}</strong>
        <small>{data.resources.policy_id}</small>
        <code>{data.resources.estimates.feature_occurrences} occurrences · {data.resources.estimates.graph_edges} edges · {data.resources.estimates.work_items} work items</code>
      </article>
    </div>
    {data.blockers.length ? <div className="graph-blockers">
      <header><div><strong>{chinese ? "还有配置问题需要处理" : "Configuration issues still need attention"}</strong><p>{chinese ? `解决以下 ${data.blockers.length} 项后即可编译；系统不会静默猜测缺失配置。` : `Resolve these ${data.blockers.length} issues before compiling; missing configuration is never guessed silently.`}</p></div><span>{data.blockers.length}</span></header>
      <div className="review-issue-list">{data.blockers.map((item) => {
        const explanation = explainBlocker(item, chinese);
        return <article className="review-issue-card" key={`${item.layer}-${item.object_key}-${item.reason_codes.join("-")}`}>
          <div className="review-issue-heading"><span>{researchLabel(item.layer, chinese ? "zh-CN" : "en")}</span><code>{item.object_key}</code></div>
          <h3>{explanation.title}</h3>
          <p>{explanation.detail}</p>
          <small><strong>{chinese ? "影响" : "Impact"}</strong><span>{explanation.impact}</span></small>
          {item.feature_keys?.length ? <div className="review-issue-features">{item.feature_keys.map((key) => <code key={key}>{researchLabel(key, chinese ? "zh-CN" : "en")}</code>)}</div> : null}
          <div className="review-issue-reasons">{item.reason_codes.map((reason) => <code key={reason}>{reason}</code>)}</div>
          <button type="button" onClick={() => onNavigate(explanation.target)}>{explanation.action}</button>
        </article>;
      })}</div>
      <button type="button" disabled>{chinese ? "编译整个研究图" : "Compile the whole research graph"}</button>
    </div> : <div className="graph-ready">
      <strong>{chinese ? "派生状态与资源准入合法" : "Derived state and resource admission are valid"}</strong>
      <button type="button" disabled={disabled} onClick={onCompile}>{chinese ? "编译整个研究图" : "Compile the whole research graph"}</button>
      {compiledFingerprint ? <code>{compiledFingerprint}</code> : null}
      {compiledFingerprint ? <button type="button" disabled={disabled} onClick={onProceed}>
        {chinese ? "前往实验确认" : "Review and start experiment"}
      </button> : null}
    </div>}
  </section>;
}

function ExperimentLaunchPanel({ data, chinese, compiledFingerprint, busy, runtimeReadiness, runtimeReadinessLoading, runtimeReadinessError, onBack, onStart }: { data: GraphDerivedView; chinese: boolean; compiledFingerprint: string | null; busy: boolean; runtimeReadiness: GraphSuiteRuntimeReadinessResponse | null; runtimeReadinessLoading: boolean; runtimeReadinessError: string | null; onBack: () => void; onStart: (frequencies: Array<"weekly" | "monthly">) => void }) {
  const selectedAggregations = data.aggregations.filter((item) => item.selected);
  const selectedDefenses = data.defenses.filter((item) => item.selected);
  const [frequencies, setFrequencies] = useState<Array<"weekly" | "monthly">>([
    "weekly",
    "monthly",
  ]);
  const toggleFrequency = (frequency: "weekly" | "monthly") => {
    setFrequencies((current) => current.includes(frequency)
      ? current.filter((item) => item !== frequency)
      : [...current, frequency]);
  };
  return <section className="graph-stage-panel experiment-launch-panel">
    <header><div>
      <p className="eyebrow">COMPILED GRAPH / EXPERIMENT CONFIRMATION</p>
      <h2>{chinese ? "确认实验配置并启动" : "Confirm configuration and start"}</h2>
      <p>{chinese
        ? "编译已经把当前修订冻结为不可变研究图。启动后，上游配置保持只读；如需更改，请先重置当前研究。"
        : "Compilation froze the current revision as an immutable graph. Upstream configuration remains read-only after start; reset the research to change it."}</p>
    </div>{compiledFingerprint ? <code>{compiledFingerprint}</code> : null}</header>
    {!compiledFingerprint ? <div className="graph-message error"><strong>{chinese ? "当前修订尚未编译" : "Current revision is not compiled"}</strong><button type="button" onClick={onBack}>{chinese ? "返回策略页检查并编译" : "Return to review and compile"}</button></div> : <>
      <div className="review-grid">
        <article><span>{chinese ? "最终信号" : "Final signals"}</span><strong>{data.aggregation_inputs.length}</strong>{data.aggregation_inputs.map((key) => <code key={key}>{researchLabel(key, chinese ? "zh-CN" : "en")}</code>)}</article>
        <article><span>{chinese ? "聚合方式" : "Aggregation"}</span><strong>{selectedAggregations.length}</strong>{selectedAggregations.map((item) => <code key={item.family_key}>{researchLabel(item.family_key, chinese ? "zh-CN" : "en")}</code>)}</article>
        <article><span>{chinese ? "策略分支 / 结果单元" : "Branches / cells"}</span><strong>{data.summary.strategy_branch_count} / {data.summary.backtest_cell_count}</strong><small>{data.frequency}</small></article>
        <article><span>{chinese ? "防御方案" : "Defense"}</span><strong>{selectedDefenses.length}</strong>{selectedDefenses.map((item) => <code key={item.variant_key}>{researchLabel(item.variant_key, chinese ? "zh-CN" : "en")}</code>)}</article>
      </div>
      <fieldset className="experiment-frequency-choice">
        <legend>{chinese ? "实验频率" : "Experiment frequencies"}</legend>
        <p>{chinese
          ? "默认同时创建周频和月频两个独立实验；二者使用相同研究配置，但分别冻结、运行和排行。"
          : "By default, launch separate weekly and monthly experiments from the same research configuration. They remain independently frozen, executed, and ranked."}</p>
        {(["weekly", "monthly"] as const).map((frequency) => <label key={frequency}>
          <input
            type="checkbox"
            checked={frequencies.includes(frequency)}
            onChange={() => toggleFrequency(frequency)}
          />
          <span>{frequency === "weekly"
            ? (chinese ? "周频" : "Weekly")
            : (chinese ? "月频" : "Monthly")}</span>
        </label>)}
      </fieldset>
      {runtimeReadiness?.ready ? <div className="graph-message success">
        <strong>{chinese ? "回测运行服务已就绪" : "Backtest runtime is ready"}</strong>
        <span>{chinese ? "启动后，实验会进入持久队列并由本地运行服务继续处理。" : "After submission, the durable local runtime will continue processing the experiment."}</span>
      </div> : <div className="graph-message error">
        <strong>{chinese ? "回测运行服务尚未就绪" : "Backtest runtime is not ready"}</strong>
        <span>{runtimeReadinessLoading
          ? (chinese ? "正在检查本地运行服务……" : "Checking the local runtime…")
          : runtimeReadinessError
          ? runtimeReadinessError
          : `${chinese ? "当前状态" : "Current state"}: ${runtimeReadiness?.state ?? "unavailable"}`}</span>
      </div>}
      <div className="graph-ready experiment-launch-actions"><button type="button" onClick={onBack}>{chinese ? "返回修改" : "Back"}</button><button type="button" disabled={busy || !runtimeReadiness?.ready || frequencies.length === 0} onClick={() => onStart(frequencies)}>{busy ? (chinese ? "正在启动…" : "Starting…") : (chinese ? `启动 ${frequencies.length} 个频率实验` : `Start ${frequencies.length} frequency experiment${frequencies.length === 1 ? "" : "s"}`)}</button></div>
    </>}
  </section>;
}

function CascadeDialog({ preview, chinese, busy, onCancel, onConfirm }: { preview: GraphChangePreviewResponse; chinese: boolean; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const dialogRef = useDialogFocus(() => { if (!busy) onCancel(); });
  const removed = preview.impact.removed_explicit_occurrences as string[] | undefined;
  if (preview.impact.change_type === "rebase_catalog") {
    return <div className="graph-drawer-backdrop"><aside ref={dialogRef} className="graph-lineage-drawer" role="dialog" aria-modal="true" aria-label="Confirm Catalog rebase"><header><div><p className="eyebrow">CATALOG REBASE PREVIEW</p><h2>Catalog rebase preview</h2></div></header><p><code>{String(preview.impact.from_catalog_release_id)}</code> → <code>{String(preview.impact.to_catalog_release_id)}</code></p>{removed?.map((item) => <code key={item}>{item}</code>)}<div className="graph-variant-actions"><button type="button" disabled={busy} onClick={onCancel}>Keep current Catalog</button><button type="button" disabled={busy} onClick={onConfirm}>Confirm rebase</button></div></aside></div>;
  }
  return <div className="graph-drawer-backdrop"><aside ref={dialogRef} className="graph-lineage-drawer" role="dialog" aria-modal="true" aria-label={chinese ? "级联取消确认" : "Confirm cascade deselection"}><header><div><p className="eyebrow">CASCADE CHANGE PREVIEW</p><h2>{chinese ? "该上游仍被下游使用" : "This ancestor is still in use"}</h2></div></header><p>{chinese ? "确认后，下列显式下游选择也会一并取消：" : "Confirming also removes these explicit downstream selections:"}</p>{removed?.map((item) => <code key={item}>{item}</code>)}<div className="graph-variant-actions"><button type="button" disabled={busy} onClick={onCancel}>{chinese ? "保留当前血缘" : "Keep current lineage"}</button><button type="button" disabled={busy} onClick={onConfirm}>{chinese ? "确认级联取消" : "Confirm cascade"}</button></div></aside></div>;
}

function ResetResearchDialog({ chinese, busy, onCancel, onConfirm }: { chinese: boolean; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const dialogRef = useDialogFocus(() => { if (!busy) onCancel(); });
  return <div className="graph-drawer-backdrop"><aside ref={dialogRef} className="graph-lineage-drawer" role="dialog" aria-modal="true" aria-label={chinese ? "确认重置当前研究" : "Confirm research reset"}>
    <header><div><p className="eyebrow">CURRENT RESEARCH RESET</p><h2>{chinese ? "确认清空这一轮研究？" : "Reset this research round?"}</h2></div></header>
    <p>{chinese
      ? "确认后将终止尚未完成的实验，立即从当前实验页隐藏未晋升的普通结果，并由后台安全清理其独占产物。新一轮只保留默认周频，资产、因子、模型、策略和防御均为空。已发布 Product、其冻结回测 Evidence 与完整血缘永久保留。"
      : "This stops unfinished experiments, immediately hides ordinary unpromoted results, and schedules their exclusively owned artifacts for safe background cleanup. The new round keeps only weekly as the UI default; assets, factors, models, strategy, and defense are empty. Published Products, their frozen backtest Evidence, and complete lineage are permanently retained."}</p>
    <div className="graph-variant-actions"><button type="button" disabled={busy} onClick={onCancel}>{chinese ? "保留当前研究" : "Keep current research"}</button><button type="button" className="danger" disabled={busy} onClick={onConfirm}>{chinese ? "确认重置" : "Confirm reset"}</button></div>
  </aside></div>;
}

function LineageDrawer({ item, chinese, onClose }: { item: Occurrence; chinese: boolean; onClose: () => void }) {
  const dialogRef = useDialogFocus(onClose);
  return <div className="graph-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><aside ref={dialogRef} className="graph-lineage-drawer" role="dialog" aria-modal="true" aria-label={chinese ? "血缘检查器" : "Lineage inspector"}><header><div><p className="eyebrow">BLOODLINE INSPECTOR</p><h2>{item.name}</h2><code>{item.feature_key}@{item.stage_no}</code></div><button type="button" aria-label={chinese ? "关闭血缘" : "Close lineage"} onClick={onClose}>×</button></header><dl><Fact term={chinese ? "产生方式" : "Producer"} value={item.producer.kind} /><Fact term={chinese ? "节点" : "Node"} value={item.producer.node_variant_key ?? "—"} /><Fact term={chinese ? "输出端口" : "Output port"} value={item.producer.output_port_key ?? "—"} /><Fact term={chinese ? "起源层" : "Origin stage"} value={item.origin_stage} /><Fact term={chinese ? "下游锁定" : "Locked by"} value={item.locked_by.join(" · ") || "—"} /><Fact term={chinese ? "选择影响" : "Selection effect"} value={`${item.select_effect.ancestor_count} ancestors · ${item.select_effect.projection_count} projections`} /></dl><p>{chinese ? "连线和计算节点均来自已发布的人工 Catalog。" : "Edges and nodes come only from the published human-designed Catalog."}</p></aside></div>;
}

function useDialogFocus(onClose: () => void) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const dialog = dialogRef.current;
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    if (!dialog) return;
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ));
    focusable()[0]?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    dialog.addEventListener("keydown", handleKeyDown);
    return () => {
      dialog.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, []);
  return dialogRef;
}

function Fact({ term, value }: { term: string; value: string | number }) {
  return <div><dt>{term}</dt><dd>{value}</dd></div>;
}

function downloadStageManifest(stage: StageView) {
  const occurrences = stage.families.flatMap((family) => family.variants).filter((item) => item.is_present);
  const blob = new Blob([JSON.stringify({ stage_no: stage.stage_no, occurrences }, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `v022-stage-${stage.stage_no}-lineage-manifest.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
