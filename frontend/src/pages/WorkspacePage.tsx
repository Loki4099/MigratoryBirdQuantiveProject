import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { useWorkspaceSelection } from "../workspace/WorkspaceSelectionContext";

const stages = [
  { key: "assets", route: "/assets", domains: ["catalog", "data"] },
  { key: "factors", route: "/factors", domains: ["factor"] },
  { key: "signals", route: "/signals", domains: ["signal"] },
  { key: "models", route: "/models", domains: ["model"] },
  { key: "strategies", route: "/strategies", domains: ["strategy"] },
] as const;

export function WorkspacePage() {
  const { t, i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage === "zh-CN";
  const navigate = useNavigate();
  const workspace = useWorkspaceSelection();
  const defaultApplied = useRef(false);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities });
  const artifacts = useQuery({ queryKey: ["artifacts", "published", "workspace"], queryFn: () => api.artifacts() });
  const gates = useQuery({ queryKey: ["release-gates"], queryFn: api.releaseGates });
  const sampleAssets = useQuery({ queryKey: ["assets", "workspace-default-sample"], queryFn: () => api.assets({ limit: 1 }) });
  const options = useQuery({
    queryKey: ["workspace", "options", workspace.frequency, workspace.assetSecurityIds, workspace.assetDataInputs, workspace.factorVariantKeys, workspace.signalVersionKeys, workspace.modelPresetKeys, workspace.modelTargetKeys, workspace.strategyPresetKeys],
    queryFn: () => api.workspaceOptions({
      frequency: workspace.frequency,
      factorVariantKeys: workspace.factorVariantKeys,
      signalVersionKeys: workspace.signalVersionKeys,
      modelPresetKeys: workspace.modelPresetKeys,
      modelTargetKeys: workspace.modelTargetKeys,
      strategyPresetKeys: workspace.strategyPresetKeys,
      assetSecurityIds: workspace.assetSecurityIds,
      assetDataInputs: workspace.assetDataInputs,
    }),
    placeholderData: (previous) => previous,
  });
  const preview = useMutation({ mutationFn: () => api.workspaceCompilePreview(workspace) });
  const duplicateDraft = useMutation({
    mutationFn: () => api.saveWorkspaceDraft({
      draftKey: `copy-${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}`,
      name: "Copy of local research draft",
      expectedRevision: null,
      selection: workspace,
    }),
  });
  const submitSuite = useMutation({
    mutationFn: async () => {
      const savedRevision = await workspace.saveNow();
      if (savedRevision === null) throw new Error("Workspace draft is not ready to submit");
      return api.submitWorkspaceSuite(
        savedRevision,
        gates.data?.formal_enabled === true ? "formal" : "exploratory",
      );
    },
    onSuccess: (submitted) => {
      const params = new URLSearchParams();
      params.set("suite", submitted.research_suite_id);
      params.set("lang", i18n.resolvedLanguage ?? "zh-CN");
      navigate(`/experiments?${params.toString()}`);
    },
  });
  const suiteStatus = useQuery({
    queryKey: ["workspace", "suite", submitSuite.data?.research_suite_id],
    queryFn: () => api.workspaceSuiteStatus(String(submitSuite.data?.research_suite_id)),
    enabled: Boolean(submitSuite.data?.research_suite_id),
    refetchInterval: (query) => query.state.data?.complete ? false : 2_000,
  });
  const cancelSuite = useMutation({
    mutationFn: () => api.cancelWorkspaceSuite(String(submitSuite.data?.research_suite_id)),
    onSuccess: () => void suiteStatus.refetch(),
  });

  useEffect(() => {
    if (defaultApplied.current || !workspace.draftReady || sampleAssets.isLoading) return;
    defaultApplied.current = true;
    if (!workspace.draftMissing) return;
    if (workspace.assetSecurityIds.length || workspace.factorVariantKeys.length || workspace.signalVersionKeys.length || workspace.modelPresetKeys.length || workspace.strategyPresetKeys.length) return;
    const sample = sampleAssets.data?.asset_sets.find((item) => item.set_key === "us_style_rotation_4_etf_sample_v1");
    if (!sample) return;
    workspace.replace({
      frequency: "weekly",
      assetSecurityIds: sample.member_security_ids.map(String),
      assetDataInputs: Object.fromEntries(
        sample.member_security_ids.map((securityId) => [String(securityId), ["canonical_market_bars"]]),
      ),
      factorVariantKeys: ["total_return__w120"],
      signalVersionKeys: ["return_continuation__total_return__w120"],
      modelPresetKeys: ["single_signal__identity_v1"],
      modelTargetKeys: ["cross_sectional_relative_return__h5"],
      strategyPresetKeys: ["multi_etf_top_k__k2__none__none__none"],
    });
  // Apply the sample only when neither the browser nor the server has a draft.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.draftReady, workspace.draftMissing, sampleAssets.isLoading, sampleAssets.data]);

  if (health.isLoading || capabilities.isLoading || artifacts.isLoading || options.isLoading) return <LoadingState />;
  const error = health.error ?? capabilities.error ?? artifacts.error ?? options.error;
  if (error) return <ErrorState error={error} retry={() => void health.refetch()} />;

  const availableDomains = new Set(capabilities.data?.domains.filter((domain) => domain.availability === "available").map((domain) => domain.key));
  const modelAvailability = new Map(options.data?.model_families.flatMap((family) => family.presets.map((preset) => [preset.preset_key, preset.selectable] as const)));
  const strategyAvailability = new Map(options.data?.strategy_families?.flatMap((family) => family.presets.map((preset) => [preset.preset_key, preset.selectable] as const)) ?? []);
  const factorAvailability = new Map(options.data?.factor_families.flatMap((family) => family.variants.map((variant) => [variant.key, variant.selectable] as const)) ?? []);
  const signalAvailability = new Map(options.data?.signal_families.flatMap((family) => family.versions.map((version) => [version.version_key, version.selectable] as const)) ?? []);
  const targetKeys = new Set(options.data?.model_target_options.map((target) => target.target_key) ?? []);
  const invalidAssets = Math.max(0, workspace.assetSecurityIds.length - (options.data?.usable_asset_count ?? 0));
  const invalidAssetInputs = (options.data?.asset_data_input_blockers ?? []).length;
  const invalidFactors = workspace.factorVariantKeys.filter((key) => !factorAvailability.get(key));
  const invalidSignals = workspace.signalVersionKeys.filter((key) => !signalAvailability.get(key));
  const invalidModels = workspace.modelPresetKeys.filter((key) => !modelAvailability.get(key));
  const invalidTargets = workspace.modelTargetKeys.filter((key) => !targetKeys.has(key));
  const invalidStrategies = workspace.strategyPresetKeys.filter((key) => !strategyAvailability.get(key));
  const invalidSelectionCount = invalidAssets + invalidAssetInputs + invalidFactors.length + invalidSignals.length + invalidModels.length + invalidTargets.length + invalidStrategies.length;
  const modelInstanceCount = workspace.modelPresetKeys.length * workspace.modelTargetKeys.length;
  const strategyBranchCount = modelInstanceCount * workspace.strategyPresetKeys.length;
  const navSearch = `?lang=${i18n.resolvedLanguage ?? "zh-CN"}&frequency=${workspace.frequency}`;
  const counts: Record<(typeof stages)[number]["key"], number> = {
    assets: workspace.assetSecurityIds.length,
    factors: workspace.factorVariantKeys.length,
    signals: workspace.signalVersionKeys.length,
    models: workspace.modelPresetKeys.length,
    strategies: workspace.strategyPresetKeys.length,
  };

  return <div className="page workspace-page">
    <header className="page-heading workspace-heading"><div><p className="eyebrow">WORKSPACE / TARGETED RESEARCH</p><h1>{t("workspace.title")}</h1><p>{t("workspace.subtitle")}</p></div><QualityBadge state={health.data?.quality.state ?? "partial"} /></header>

    <section className="scope-strip workspace-summary">
      <div><span>{t("workspace.draft")}</span><strong>{workspace.draftRevision ? `v${workspace.draftRevision}` : t("workspace.unsaved")}</strong></div>
      <div><span>{t("workspace.published")}</span><strong>{artifacts.data?.total ?? 0}</strong></div>
      <div><span>{t("workspace.branches")}</span><strong>{strategyBranchCount}</strong></div>
      <div><span>{t("workspace.cells")}</span><strong>{modelInstanceCount} + {strategyBranchCount * 6}</strong></div>
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">ASSET → FACTOR → SIGNAL → MODEL → STRATEGY</p><h2>{t("workspace.chain")}</h2></div><span className="workspace-version">{health.data?.database_revision}</span></div>
      <div className="workspace-stage-grid">{stages.map((stage, index) => {
        const available = stage.domains.every((domain) => availableDomains.has(domain));
        return <Link className="workspace-stage-card" to={{ pathname: stage.route, search: navSearch }} key={stage.key}><span>{String(index + 1).padStart(2, "0")}</span><div><h3>{t(`workspace.stage.${stage.key}`)}</h3><p>{t(`workspace.stageHint.${stage.key}`)}</p></div><div className="workspace-stage-state"><strong>{counts[stage.key]}</strong><QualityBadge state={available ? "ok" : "partial"} /></div></Link>;
      })}</div>
    </section>

    <section className="workspace-control-strip">
      <label>{t("workspace.frequency")}<select value={workspace.frequency} onChange={(event) => workspace.setFrequency(event.target.value as "weekly" | "monthly")}><option value="weekly">{chinese ? "周频" : "Weekly"}</option><option value="monthly">{chinese ? "月频" : "Monthly"}</option></select></label>
      <div><span>{chinese ? "组件目录" : "Component catalog"}</span><code>{options.data?.catalog_version ?? "—"}</code></div>
      <div><span>{chinese ? "无效选择" : "Invalid selections"}</span><strong className={invalidSelectionCount ? "workspace-invalid" : ""}>{invalidSelectionCount}</strong></div>
      <button onClick={workspace.clear}>{chinese ? "清空草稿" : "Clear draft"}</button>
      <button onClick={() => void workspace.saveNow().catch(() => undefined)} disabled={workspace.draftSaving || workspace.draftConflict}>{workspace.draftSaving ? (chinese ? "保存中…" : "Saving…") : (workspace.draftDirty ? (chinese ? "保存草稿" : "Save draft") : (chinese ? "已保存" : "Saved"))}</button>
      <button onClick={() => duplicateDraft.mutate()} disabled={duplicateDraft.isPending}>{chinese ? "复制草稿" : "Copy draft"}</button>
      <button onClick={() => preview.mutate()} disabled={preview.isPending}>{chinese ? "编译预览" : "Compile preview"}</button>
      <button onClick={() => submitSuite.mutate()} disabled={submitSuite.isPending || workspace.draftSaving || !workspace.draftReady || workspace.draftConflict}>{submitSuite.isPending ? (chinese ? "正在保存并创建实验…" : "Saving and creating experiment…") : gates.data?.formal_enabled === true ? (chinese ? "创建正式实验套件" : "Create formal Suite") : (chinese ? "运行探索性实验" : "Run exploratory experiment")}</button>
    </section>

    {workspace.draftError ? <ErrorState error={workspace.draftError instanceof Error ? workspace.draftError : new Error(String(workspace.draftError))} retry={workspace.draftConflict ? () => void workspace.reloadFromServer() : () => void workspace.saveNow().catch(() => undefined)} /> : null}
    {workspace.draftConflict ? <section className="workspace-release-gate"><strong>{chinese ? "草稿已在另一个窗口更新" : "The draft changed in another window"}</strong><span>{chinese ? "请选择载入服务端版本，或把当前本地选择另存为副本。系统已停止自动重试。" : "Load the server version or save the current local selection as a copy. Automatic retries are paused."} <button type="button" onClick={() => void workspace.reloadFromServer()}>{chinese ? "载入服务端版本" : "Load server version"}</button> <button type="button" onClick={() => duplicateDraft.mutate()} disabled={duplicateDraft.isPending}>{chinese ? "另存本地副本" : "Save local copy"}</button></span></section> : null}
    {submitSuite.error ? <ErrorState error={submitSuite.error} retry={() => submitSuite.mutate()} /> : null}
    {gates.data && !gates.data.formal_enabled ? <section className="workspace-release-gate"><strong>{chinese ? "正式提交尚未开放；探索性实验可运行" : "Formal submission is not open; exploratory experiments are available"}</strong><span>{chinese ? "探索结果使用静态已选资产与线性交易成本，不包含PIT、退市事件或冲击成本证据；表现合格时可升级为带警告的样本外研究候选，但不得宣称为PIT无偏、100M可部署或成本已校准的正式产品。正式门禁：" : "Exploratory results use static selected assets and linear costs without PIT, delisting-event, or calibrated-impact evidence. A qualified result may be promoted to a warning-bearing OOS research candidate, but must not be presented as PIT-unbiased, deployable at 100M, or cost-calibrated. Formal gates: "}{Array.isArray(gates.data.reason_codes) ? gates.data.reason_codes.join(" · ") : "release_gate_status_unavailable"}</span></section> : null}
    {submitSuite.data ? <section className="workspace-release-gate"><strong>{submitSuite.data.suite_mode === "exploratory" ? "Exploratory Suite" : "Formal Suite"} {suiteStatus.data?.complete ? "complete" : "queued"}</strong><span>{submitSuite.data.suite_key} · {suiteStatus.data ? `${suiteStatus.data.terminal}/${suiteStatus.data.total} terminal` : `${submitSuite.data.queued_work_item_count} work items`} <button onClick={() => cancelSuite.mutate()} disabled={cancelSuite.isPending || suiteStatus.data?.complete}>Cancel Suite</button></span></section> : null}

    {preview.error ? <ErrorState error={preview.error} retry={() => preview.mutate()} /> : null}
    {preview.data ? <section className={`catalog-section workspace-compile-result ${preview.data.compiled.runnable ? "runnable" : "blocked"}`}>
      <div className="section-heading"><div><p className="eyebrow">COMPILE / IMMUTABLE PREVIEW</p><h2>{preview.data.compiled.runnable ? "Ready to create Suite" : "Blocked selection"}</h2></div><code>{String(preview.data.compiled.specification_fingerprint)}</code></div>
      <div className="scope-strip"><div><span>Usable assets</span><strong>{preview.data.usable_asset_count} / {preview.data.selected_asset_count}</strong></div><div><span>Model instances</span><strong>{Array.isArray(preview.data.compiled.model_instances) ? preview.data.compiled.model_instances.length : 0}</strong></div><div><span>Strategy branches</span><strong>{Array.isArray(preview.data.compiled.strategy_branches) ? preview.data.compiled.strategy_branches.length : 0}</strong></div><div><span>Portfolio cells</span><strong>{String(preview.data.compiled.portfolio_cell_count)}</strong></div></div>
      {preview.data.blockers.length ? <div className="workspace-compile-blockers">{preview.data.blockers.map((blocker) => <p key={`${blocker.layer}-${blocker.object_key}`}><strong>{blocker.layer} · {blocker.object_key}</strong><span>{blocker.reason_codes.join(" · ")}</span></p>)}</div> : <p className="scope-note">All selected components compile without silent dropping. Each branch will create Full/3Y/1Y × 5/10bps+impact.</p>}
    </section> : null}

    <section className="workspace-default-card"><div><p className="eyebrow">DEFAULT SAMPLE SETTING</p><h2>US Style Rotation 4 ETF Sample v1</h2><p>{t("workspace.sampleNote")}</p></div><dl><div><dt>{t("workspace.assets")}</dt><dd>IWF · IWD · IWO · IWN</dd></div><div><dt>{t("workspace.frequency")}</dt><dd>{t("workspace.weekly")}</dd></div><div><dt>K</dt><dd>2</dd></div><div><dt>{t("workspace.matrix")}</dt><dd>3 × 2 = 6</dd></div></dl><Link className="arrow-link" to={{ pathname: "/assets", search: navSearch }}>{t("workspace.start")} →</Link></section>
  </div>;
}
