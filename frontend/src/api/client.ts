import type { components } from "./schema.generated";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type CapabilitiesResponse = components["schemas"]["CapabilitiesResponse"];
export type SessionContextResponse = components["schemas"]["SessionContextResponse"];
export type V022ReleaseControlResponse = components["schemas"]["V022ReleaseControlResponse"];
export type ArtifactListResponse = components["schemas"]["ArtifactListResponse"];
export type ArtifactDetailResponse = components["schemas"]["ArtifactDetailResponse"];
export type LineageManifestResponse = components["schemas"]["LineageManifestResponse"];
export type AssetCatalogResponse = components["schemas"]["AssetCatalogResponse"];
export type AssetSeriesResponse = components["schemas"]["AssetSeriesResponse"];
export type AssetDataExportPreviewResponse = components["schemas"]["AssetDataExportPreviewResponse"];
export type AssetDataExportJobResponse = components["schemas"]["AssetDataExportJobResponse"];
export type WorkspaceOptionsResponse = components["schemas"]["WorkspaceOptionsResponse"];
export type WorkspaceCompilePreviewResponse = components["schemas"]["WorkspaceCompilePreviewResponse"];
export type WorkspaceDraftResponse = components["schemas"]["WorkspaceDraftResponse"];
export type ReleaseGateResponse = components["schemas"]["ReleaseGateResponse"];
export type WorkspaceSuiteSubmitResponse = components["schemas"]["WorkspaceSuiteSubmitResponse"];
export type WorkspaceSuiteStatusResponse = components["schemas"]["WorkspaceSuiteStatusResponse"];
export type WorkspaceSuiteCancelResponse = components["schemas"]["WorkspaceSuiteCancelResponse"];
export type GraphWorkspacePreviewResponse = components["schemas"]["GraphWorkspacePreviewResponse"];
export type GraphDraftDerivedViewResponse = components["schemas"]["GraphDraftDerivedViewResponse"];
export type GraphDraftSnapshotResponse = components["schemas"]["GraphDraftSnapshotResponse"];
export type GraphChangePreviewResponse = components["schemas"]["GraphChangePreviewResponse"];
export type GraphDraftCompileResponse = components["schemas"]["GraphDraftCompileResponse"];
export type GraphSuiteSubmitResponse = components["schemas"]["GraphSuiteSubmitResponse"];
export type GraphSuiteLaunchBatchResponse = components["schemas"]["GraphSuiteLaunchBatchResponse"];
export type GraphSuiteStatusResponse = components["schemas"]["GraphSuiteStatusResponse"];
export type GraphSuiteListResponse = components["schemas"]["GraphSuiteListResponse"];
export type GraphSuiteResultsResponse = components["schemas"]["GraphSuiteResultsResponse"];
export type GraphSuiteRuntimeReadinessResponse = components["schemas"]["GraphSuiteRuntimeReadinessResponse"];
export type GraphStageFamilyPageResponse = components["schemas"]["GraphStageFamilyPageResponse"];
export type PromotionQualificationResponse = components["schemas"]["PromotionQualificationResponse"];
export type ProductPromotionResponse = components["schemas"]["ProductPromotionResponse"];
export type ProductLifecycleChangeResponse = components["schemas"]["ProductLifecycleChangeResponse"];
export type ProductAlertChangeResponse = components["schemas"]["ProductAlertChangeResponse"];
export type ProductReviewResponse = components["schemas"]["ProductReviewResponse"];
export type ProductCatalogResponse = components["schemas"]["ProductCatalogResponse"];
export type ProductDetailResponse = components["schemas"]["ProductDetailResponse"];
export type ProductRecommendationResponse = components["schemas"]["ProductRecommendationResponse"];
export type DataRequirementResponse = components["schemas"]["DataRequirementResponse"];
export type DataOverviewResponse = components["schemas"]["DataOverviewResponse"];
export type FactorOverviewResponse = components["schemas"]["FactorOverviewResponse"];
export type SignalOverviewResponse = components["schemas"]["SignalOverviewResponse"];
export type SignalResearchExportJobResponse = components["schemas"]["SignalResearchExportJobResponse"];
export type ModelOverviewResponse = components["schemas"]["ModelOverviewResponse"];
export type StrategyOverviewResponse = components["schemas"]["StrategyOverviewResponse"];
export type StrategyTargetPathResponse = components["schemas"]["StrategyTargetPathResponse"];
export type ExperimentOverviewResponse = components["schemas"]["ExperimentOverviewResponse"];
export type ExperimentResultResponse = components["schemas"]["ExperimentResultResponse"];
export type ProductRankingResponse = components["schemas"]["ProductRankingResponse"];
export type ProductCompareResponse = components["schemas"]["ProductCompareResponse"];
export type DecisionExplorerResponse = components["schemas"]["DecisionExplorerResponse"];
export type V022ExperimentIdentityCatalogResponse = components["schemas"]["V022ExperimentIdentityCatalogResponse"];
export type V022ExperimentIdentityDetailResponse = components["schemas"]["V022ExperimentIdentityDetailResponse"];
export type V022ExperimentLeaderboardResponse = components["schemas"]["V022ExperimentLeaderboardResponse"];
export type V022ExperimentSeriesResponse = components["schemas"]["V022ExperimentSeriesResponse"];
export type V022ProductCandidateResponse = components["schemas"]["V022ProductCandidateResponse"];
export type V022ProductPromotionResponse = components["schemas"]["V022ProductPromotionResponse"];
export type V022ProductEnrollmentResponse = components["schemas"]["V022ProductEnrollmentResponse"];
export type V022ProductIdentityCatalogResponse = components["schemas"]["V022ProductIdentityCatalogResponse"];
export type V022ProductIdentityDetailResponse = components["schemas"]["V022ProductIdentityDetailResponse"];
export type V022ProductLifecycleResponse = components["schemas"]["V022ProductLifecycleResponse"];

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as {
      message?: string;
      code?: string;
      details?: Record<string, unknown>;
    };
    throw new ApiClientError(
      payload.message ?? `${response.status} ${response.statusText}`,
      response.status,
      payload.code,
      payload.details,
    );
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { message?: string; code?: string; details?: Record<string, unknown> };
    throw new ApiClientError(payload.message ?? `${response.status} ${response.statusText}`, response.status, payload.code, payload.details);
  }
  return await response.json() as T;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { message?: string; code?: string; details?: Record<string, unknown> };
    throw new ApiClientError(payload.message ?? `${response.status} ${response.statusText}`, response.status, payload.code, payload.details);
  }
  return await response.json() as T;
}

let sessionPromise: Promise<SessionContextResponse> | undefined;

function authenticatedSession(): Promise<SessionContextResponse> {
  sessionPromise ??= getJson<SessionContextResponse>("/api/v2/session").catch((error: unknown) => {
    sessionPromise = undefined;
    throw error;
  });
  return sessionPromise;
}

async function authenticatedActorKey(): Promise<string> {
  return (await authenticatedSession()).actor_key;
}

interface AssetCatalogFilters {
  search?: string;
  category?: string;
  maturity?: string;
  tradability?: string;
  limit?: number;
  offset?: number;
}

function assetCatalogPage(filters: AssetCatalogFilters = {}) {
  const search = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  });
  const suffix = search.size ? `?${search.toString()}` : "";
  return getJson<AssetCatalogResponse>(`/api/v2/catalog/assets${suffix}`);
}

export const api = {
  health: () => getJson<HealthResponse>("/api/v2/health"),
  session: authenticatedSession,
  releaseControl: () => getJson<V022ReleaseControlResponse>("/api/v2/release-control"),
  capabilities: () => getJson<CapabilitiesResponse>("/api/v2/capabilities"),
  assets: assetCatalogPage,
  allAssets: async (filters: Omit<AssetCatalogFilters, "limit" | "offset"> = {}) => {
    const first = await assetCatalogPage({ ...filters, limit: 200, offset: 0 });
    const items = [...first.items];
    while (items.length < first.total) {
      const page = await assetCatalogPage({ ...filters, limit: 200, offset: items.length });
      if (!page.items.length) break;
      items.push(...page.items);
    }
    return { ...first, items, limit: items.length, offset: 0 };
  },
  assetSeries: (securityId: string) =>
    getJson<AssetSeriesResponse>(`/api/v2/catalog/assets/${securityId}/series`),
  workspaceOptions: (input: {
    frequency: "weekly" | "monthly";
    factorVariantKeys: string[];
    signalVersionKeys: string[];
    modelPresetKeys?: string[];
    modelTargetKeys?: string[];
    strategyPresetKeys?: string[];
    assetSecurityIds?: string[];
    assetDataInputs?: Record<string, string[]>;
  }) => {
    const search = new URLSearchParams({ frequency: input.frequency });
    input.factorVariantKeys.forEach((key) => search.append("selected_factor_variant", key));
    input.signalVersionKeys.forEach((key) => search.append("selected_signal", key));
    input.modelPresetKeys?.forEach((key) => search.append("selected_model", key));
    input.modelTargetKeys?.forEach((key) => search.append("selected_target", key));
    input.strategyPresetKeys?.forEach((key) => search.append("selected_strategy", key));
    input.assetSecurityIds?.forEach((key) => search.append("selected_asset", key));
    if (input.assetDataInputs) {
      input.assetSecurityIds?.forEach((securityId) => {
        const selectedInputs = input.assetDataInputs?.[securityId] ?? [];
        if (!selectedInputs.length) search.append("selected_asset_data_input", `${securityId}:`);
        selectedInputs.forEach((inputKey) => search.append("selected_asset_data_input", `${securityId}:${inputKey}`));
      });
    }
    return getJson<WorkspaceOptionsResponse>(`/api/v2/workspace/options?${search.toString()}`);
  },
  workspaceCompilePreview: (input: {
    frequency: "weekly" | "monthly";
    assetSecurityIds: string[];
    assetDataInputs: Record<string, string[]>;
    factorVariantKeys: string[];
    signalVersionKeys: string[];
    modelPresetKeys: string[];
    modelTargetKeys: string[];
    strategyPresetKeys: string[];
  }) => postJson<WorkspaceCompilePreviewResponse>("/api/v2/workspace/compile-preview", {
    frequency: input.frequency,
    asset_security_ids: input.assetSecurityIds,
    asset_data_inputs: input.assetDataInputs,
    factor_variant_keys: input.factorVariantKeys,
    signal_version_keys: input.signalVersionKeys,
    model_preset_keys: input.modelPresetKeys,
    model_target_keys: input.modelTargetKeys,
    strategy_preset_keys: input.strategyPresetKeys,
  }),
  graphWorkspacePreview: (input: {
    frequency: "weekly" | "monthly";
    explicitFeatures: Array<{ featureKey: string; stageNo: 0 | 1 | 2 | 3 }>;
    aggregationFamilyKeys: string[];
    aggregationParameterPresetKeys?: Record<string, string[]>;
    strategyKeys?: string[];
    strategyParameterPresetKeys?: Record<string, string[]>;
    defenseKeys?: string[];
  }) => postJson<GraphWorkspacePreviewResponse>("/api/v2/workspace/graph-preview", {
    frequency: input.frequency,
    explicit_features: input.explicitFeatures.map((item) => ({
      feature_key: item.featureKey,
      stage_no: item.stageNo,
    })),
    aggregation_family_keys: input.aggregationFamilyKeys,
    aggregation_parameter_preset_keys: input.aggregationParameterPresetKeys,
    strategy_keys: input.strategyKeys,
    strategy_parameter_preset_keys: input.strategyParameterPresetKeys,
    defense_keys: input.defenseKeys,
  }),
  createGraphDraft: async (input: {
    idempotencyKey: string;
    frequency?: "weekly" | "monthly";
  }) => postJson<GraphDraftSnapshotResponse>("/api/v2/workspace/graph-drafts", {
    researcher_key: await authenticatedActorKey(),
    draft_key: "browser_default_v1",
    name: "v0.22 Graph Workspace",
    idempotency_key: input.idempotencyKey,
    frequency: input.frequency ?? "weekly",
    asset_context_key: null,
    data_input_keys: [],
  }),
  graphDraft: (graphDraftId: string) =>
    getJson<GraphDraftSnapshotResponse>(
      `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}`,
    ),
  graphDraftByKey: (draftKey: string) =>
    getJson<GraphDraftSnapshotResponse>(
      `/api/v2/workspace/graph-drafts/by-key/${encodeURIComponent(draftKey)}`,
    ),
  currentGraphDraftCompile: (graphDraftId: string) =>
    getJson<GraphDraftCompileResponse>(
      `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/current-compile`,
    ),
  resetGraphDraft: async (graphDraftId: string, expectedRevision: number) =>
    postJson<GraphDraftSnapshotResponse>(
      `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/reset`,
      {
        expected_revision: expectedRevision,
        actor_key: await authenticatedActorKey(),
        idempotency_key: crypto.randomUUID(),
      },
    ),
  cloneGraphDraftRevision: async (graphDraftId: string, input: {
    sourceRevision: number;
    draftKey: string;
    name: string;
  }) => postJson<GraphDraftSnapshotResponse>(
    `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/clones`,
    {
      source_revision: input.sourceRevision,
      researcher_key: await authenticatedActorKey(),
      draft_key: input.draftKey,
      name: input.name,
      idempotency_key: crypto.randomUUID(),
    },
  ),
  graphStageFamilies: (graphDraftId: string, input: {
    stageNo: 0 | 1 | 2 | 3;
    search?: string;
    selectionFilter?: "all" | "selected" | "locked";
    availabilityFilter?: "all" | "ready" | "requires_ancestors" | "hard_incompatible";
    cursor?: string;
    limit?: number;
  }) => {
    const limit = input.limit ?? 12;
    if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
      throw new RangeError("Graph Stage Family limit must be an integer between 1 and 50");
    }
    const search = new URLSearchParams();
    if (input.search) search.set("search", input.search);
    if (input.selectionFilter) search.set("selection_filter", input.selectionFilter);
    if (input.availabilityFilter) search.set("availability_filter", input.availabilityFilter);
    if (input.cursor) search.set("cursor", input.cursor);
    search.set("limit", String(limit));
    return getJson<GraphStageFamilyPageResponse>(
      `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}`
      + `/stages/${input.stageNo}/families?${search.toString()}`,
    );
  },
  applyGraphDraftEvent: async (graphDraftId: string, input: {
    expectedRevision: number;
    eventType: string;
    event: Record<string, unknown>;
  }) => postJson<GraphDraftSnapshotResponse>(
    `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/events`,
    {
      expected_revision: input.expectedRevision,
      actor_key: await authenticatedActorKey(),
      idempotency_key: crypto.randomUUID(),
      event_type: input.eventType,
      event: input.event,
    },
  ),
  previewGraphDraftChange: async (graphDraftId: string, input: {
    expectedRevision: number;
    featureKey: string;
    stageNo: 0 | 1 | 2 | 3;
  }) => postJson<GraphChangePreviewResponse>(
    `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/change-previews`,
    {
      expected_revision: input.expectedRevision,
      actor_key: await authenticatedActorKey(),
      feature_key: input.featureKey,
      stage_no: input.stageNo,
    },
  ),
  previewGraphCatalogRebase: async (graphDraftId: string, expectedRevision: number) =>
    postJson<GraphChangePreviewResponse>(
      `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/rebase-previews`,
      { expected_revision: expectedRevision, actor_key: await authenticatedActorKey() },
    ),
  confirmGraphDraftChange: async (graphDraftId: string, impactToken: string, expectedRevision: number) =>
    postJson<GraphDraftSnapshotResponse>(
      `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/change-previews/${encodeURIComponent(impactToken)}/confirm`,
      {
        expected_revision: expectedRevision,
        actor_key: await authenticatedActorKey(),
        idempotency_key: crypto.randomUUID(),
      },
    ),
  compileGraphDraft: async (graphDraftId: string, expectedRevision: number) =>
    postJson<GraphDraftCompileResponse>(
      `/api/v2/workspace/graph-drafts/${encodeURIComponent(graphDraftId)}/compile`,
      {
        expected_revision: expectedRevision,
        actor_key: await authenticatedActorKey(),
        idempotency_key: crypto.randomUUID(),
      },
    ),
  workspaceDraft: async (draftKey = "default") => {
    const researcherId = await authenticatedActorKey();
    return getJson<WorkspaceDraftResponse>(
      `/api/v2/workspace/drafts/${encodeURIComponent(researcherId)}/${encodeURIComponent(draftKey)}`,
    );
  },
  saveWorkspaceDraft: (input: {
    draftKey?: string;
    name: string;
    expectedRevision: number | null;
    selection: {
      frequency: "weekly" | "monthly";
      assetSecurityIds: string[];
      assetDataInputs: Record<string, string[]>;
      factorVariantKeys: string[];
      signalVersionKeys: string[];
      modelPresetKeys: string[];
      modelTargetKeys: string[];
      strategyPresetKeys: string[];
    };
  }) => authenticatedActorKey().then((researcherId) => {
    const draftKey = input.draftKey ?? "default";
    return putJson<WorkspaceDraftResponse>(
      `/api/v2/workspace/drafts/${encodeURIComponent(researcherId)}/${encodeURIComponent(draftKey)}`,
      {
        idempotency_key: crypto.randomUUID(),
        researcher_id: researcherId,
        draft_key: draftKey,
        name: input.name,
        expected_revision: input.expectedRevision,
        selection: {
          frequency: input.selection.frequency,
          asset_security_ids: input.selection.assetSecurityIds,
          asset_data_inputs: input.selection.assetDataInputs,
          factor_variant_keys: input.selection.factorVariantKeys,
          signal_version_keys: input.selection.signalVersionKeys,
          model_preset_keys: input.selection.modelPresetKeys,
          model_target_keys: input.selection.modelTargetKeys,
          strategy_preset_keys: input.selection.strategyPresetKeys,
        },
      },
    );
  }),
  releaseGates: () => getJson<ReleaseGateResponse>("/api/v2/release-gates"),
  submitWorkspaceSuite: async (
    expectedRevision: number,
    suiteMode: "formal" | "exploratory" = "exploratory",
  ) =>
    postJson<WorkspaceSuiteSubmitResponse>("/api/v2/workspace/suites", {
      idempotency_key: crypto.randomUUID(),
      researcher_id: await authenticatedActorKey(),
      draft_key: "default",
      expected_revision: expectedRevision,
      suite_mode: suiteMode,
    }),
  workspaceSuiteStatus: (suiteId: string) =>
    getJson<WorkspaceSuiteStatusResponse>(`/api/v2/workspace/suites/${suiteId}`),
  submitGraphSuite: async (
    compiledResearchGraphId: string,
    idempotencyKey: string,
    graphDraftId: string,
    graphDraftRevision: number,
  ) => postJson<GraphSuiteSubmitResponse>("/api/v2/workspace/graph-suites", {
    actor_key: await authenticatedActorKey(),
    idempotency_key: idempotencyKey,
    compiled_research_graph_id: compiledResearchGraphId,
    graph_draft_id: graphDraftId,
    graph_draft_revision: graphDraftRevision,
    suite_mode: "exploratory",
  }),
  submitGraphSuiteLaunchBatch: async (input: {
    compiledResearchGraphId: string;
    idempotencyKey: string;
    graphDraftId: string;
    graphDraftRevision: number;
    frequencies: Array<"weekly" | "monthly">;
  }) => postJson<GraphSuiteLaunchBatchResponse>(
    "/api/v2/workspace/graph-suite-launch-batches",
    {
      actor_key: await authenticatedActorKey(),
      idempotency_key: input.idempotencyKey,
      source_compiled_research_graph_id: input.compiledResearchGraphId,
      source_graph_draft_id: input.graphDraftId,
      source_graph_draft_revision: input.graphDraftRevision,
      frequencies: input.frequencies,
      suite_mode: "exploratory",
    },
  ),
  graphSuiteLaunchBatchStatus: (batchId: string) =>
    getJson<GraphSuiteLaunchBatchResponse>(
      `/api/v2/workspace/graph-suite-launch-batches/${encodeURIComponent(batchId)}`,
    ),
  graphSuiteStatus: (suiteId: string) =>
    getJson<GraphSuiteStatusResponse>(`/api/v2/workspace/graph-suites/${suiteId}`),
  graphSuiteRuntimeReadiness: () =>
    getJson<GraphSuiteRuntimeReadinessResponse>(
      "/api/v2/workspace/graph-suite-runtime/readiness",
    ),
  graphSuites: (limit = 50, offset = 0) =>
    getJson<GraphSuiteListResponse>(
      `/api/v2/workspace/graph-suites?${new URLSearchParams({
        limit: String(limit), offset: String(offset),
      })}`,
    ),
  graphSuiteResults: (suiteId: string) =>
    getJson<GraphSuiteResultsResponse>(
      `/api/v2/workspace/graph-suites/${encodeURIComponent(suiteId)}/results`,
    ),
  previewAssetDataExport: async (input: {
    graphDraftId: string;
    graphDraftRevision: number;
    exportFormat: "parquet" | "csv";
    startDate?: string;
    endDate?: string;
  }) => postJson<AssetDataExportPreviewResponse>(
    "/api/v2/v022/asset-data-exports/preview",
    {
      researcher_key: await authenticatedActorKey(),
      graph_draft_id: input.graphDraftId,
      graph_draft_revision: input.graphDraftRevision,
      export_format: input.exportFormat,
      start_date: input.startDate ?? null,
      end_date: input.endDate ?? null,
    },
  ),
  createAssetDataExport: async (input: {
    graphDraftId: string;
    graphDraftRevision: number;
    exportFormat: "parquet" | "csv";
    startDate?: string;
    endDate?: string;
  }) => postJson<AssetDataExportJobResponse>(
    "/api/v2/v022/asset-data-exports",
    {
      researcher_key: await authenticatedActorKey(),
      graph_draft_id: input.graphDraftId,
      graph_draft_revision: input.graphDraftRevision,
      export_format: input.exportFormat,
      start_date: input.startDate ?? null,
      end_date: input.endDate ?? null,
    },
  ),
  assetDataExportStatus: (exportJobId: string) =>
    getJson<AssetDataExportJobResponse>(
      `/api/v2/v022/asset-data-exports/${encodeURIComponent(exportJobId)}`,
    ),
  cancelAssetDataExport: (exportJobId: string) =>
    postJson<AssetDataExportJobResponse>(
      `/api/v2/v022/asset-data-exports/${encodeURIComponent(exportJobId)}/cancel`,
      {},
    ),
  exportSelectedSignals: (input: {
    frequency: "weekly" | "monthly";
    assetSecurityIds: string[];
    assetDataInputs: Record<string, string[]>;
    signalVersionKeys: string[];
    includeTargets: boolean;
  }) => postJson<SignalResearchExportJobResponse>("/api/v2/signals/research-export.zip", {
    frequency: input.frequency,
    asset_security_ids: input.assetSecurityIds,
    asset_data_inputs: input.assetDataInputs,
    signal_version_keys: input.signalVersionKeys,
    include_targets: input.includeTargets,
  }),
  signalExportStatus: (exportJobId: string) =>
    getJson<SignalResearchExportJobResponse>(
      `/api/v2/signals/research-exports/${encodeURIComponent(exportJobId)}`,
    ),
  cancelWorkspaceSuite: (suiteId: string) =>
    postJson<WorkspaceSuiteCancelResponse>(`/api/v2/workspace/suites/${suiteId}/cancel`, {
      idempotency_key: crypto.randomUUID(),
    }),
  products: () => getJson<ProductCatalogResponse>("/api/v2/products"),
  v022Experiments: () =>
    getJson<V022ExperimentIdentityCatalogResponse>("/api/v2/v022/experiments"),
  v022Experiment: (evidenceId: string) =>
    getJson<V022ExperimentIdentityDetailResponse>(`/api/v2/v022/experiments/${evidenceId}`),
  v022ExperimentLeaderboard: (input: {
    frequency: "weekly" | "monthly";
    sort: "sharpe_ratio" | "cagr" | "cagr_spread" | "maximum_drawdown";
    limit?: number;
    offset?: number;
  }) => getJson<V022ExperimentLeaderboardResponse>(
    `/api/v2/v022/experiments/leaderboard?${new URLSearchParams({
      frequency: input.frequency,
      sort: input.sort,
      limit: String(input.limit ?? 200),
      offset: String(input.offset ?? 0),
    })}`,
  ),
  v022ExperimentSeries: (evidenceId: string, maxPoints = 600) =>
    getJson<V022ExperimentSeriesResponse>(
      `/api/v2/v022/experiments/${encodeURIComponent(evidenceId)}/series?${new URLSearchParams({
        max_points: String(maxPoints),
      })}`,
    ),
  promoteAndEnrollV022Product: async (evidenceId: string, input: {
    idempotencyKey: string;
    productKey: string;
    name: string;
    description?: string;
    versionNumber?: number;
  }) => postJson<V022ProductPromotionResponse>(
    `/api/v2/v022/experiment-results/${encodeURIComponent(evidenceId)}/promote-and-enroll`,
    {
      idempotency_key: input.idempotencyKey,
      researcher_id: await authenticatedActorKey(),
      product_key: input.productKey,
      name: input.name,
      description: input.description ?? "",
      version_number: input.versionNumber ?? 1,
    },
  ),
  promoteV022ProductCandidate: async (evidenceId: string, input: {
    idempotencyKey: string;
    productKey: string;
    name: string;
    description?: string;
    versionNumber?: number;
  }) => postJson<V022ProductCandidateResponse>(
    `/api/v2/v022/experiments/${encodeURIComponent(evidenceId)}/promote`,
    {
      idempotency_key: input.idempotencyKey,
      researcher_id: await authenticatedActorKey(),
      product_key: input.productKey,
      name: input.name,
      description: input.description ?? "",
      version_number: input.versionNumber ?? 1,
    },
  ),
  enrollV022ProductCandidate: async (executionVersionId: string, input: {
    idempotencyKey: string;
    qualificationVersionId: string;
    monitoringPolicyVersionId: string;
    scheduleKey: string;
    scheduleVersionNumber?: number;
    frequency: "weekly" | "monthly";
    sessions: Array<{ sessionDate: string; decisionCutoffAt: string }>;
    oosAnchorCutoffAt: string;
    activationEffectiveAt: string;
  }) => postJson<V022ProductEnrollmentResponse>(
    `/api/v2/v022/product-candidates/${encodeURIComponent(executionVersionId)}/enroll`,
    {
      idempotency_key: input.idempotencyKey,
      researcher_id: await authenticatedActorKey(),
      qualification_version_id: input.qualificationVersionId,
      monitoring_policy_version_id: input.monitoringPolicyVersionId,
      schedule_key: input.scheduleKey,
      schedule_version_number: input.scheduleVersionNumber ?? 1,
      frequency: input.frequency,
      sessions: input.sessions.map((item) => ({
        session_date: item.sessionDate,
        decision_cutoff_at: item.decisionCutoffAt,
      })),
      oos_anchor_cutoff_at: input.oosAnchorCutoffAt,
      activation_effective_at: input.activationEffectiveAt,
    },
  ),
  v022Products: () =>
    getJson<V022ProductIdentityCatalogResponse>("/api/v2/v022/products"),
  v022Product: (enrollmentId: string) =>
    getJson<V022ProductIdentityDetailResponse>(`/api/v2/v022/products/${enrollmentId}`),
  changeV022ProductLifecycle: async (enrollmentId: string, input: {
    idempotencyKey: string;
    expectedSequence: number;
    target: "active" | "suspended" | "retired" | "invalidated";
    reasonCode: string;
    reason: string;
    effectiveAt: string;
  }) => postJson<V022ProductLifecycleResponse>(
    `/api/v2/v022/products/${encodeURIComponent(enrollmentId)}/lifecycle`,
    {
      idempotency_key: input.idempotencyKey,
      researcher_id: await authenticatedActorKey(),
      expected_sequence: input.expectedSequence,
      target: input.target,
      reason_code: input.reasonCode,
      reason: input.reason,
      requested_at: new Date().toISOString(),
      effective_at: input.effectiveAt,
    },
  ),
  productDetail: (enrollmentId: string) =>
    getJson<ProductDetailResponse>(`/api/v2/products/${enrollmentId}`),
  productRecommendation: (enrollmentId: string) =>
    getJson<ProductRecommendationResponse>(`/api/v2/products/${enrollmentId}/recommendation`),
  changeProductLifecycle: async (enrollmentId: string, input: {
    target: "active" | "suspended" | "retired" | "invalidated";
    expectedRevision: number;
    reason: string;
    effectiveAt: string;
  }) => postJson<ProductLifecycleChangeResponse>(
    `/api/v2/products/${enrollmentId}/lifecycle`,
    {
      idempotency_key: crypto.randomUUID(),
      target: input.target,
      expected_revision: input.expectedRevision,
      reason_code: `manual_${input.target}`,
      reason: input.reason,
      researcher_id: await authenticatedActorKey(),
      requested_at: new Date().toISOString(),
      effective_at: input.effectiveAt,
    },
  ),
  changeProductAlert: async (
    alertId: string,
    target: "acknowledged" | "resolved" | "superseded",
  ) => postJson<ProductAlertChangeResponse>(
    `/api/v2/products/alerts/${alertId}/status`,
    {
      idempotency_key: crypto.randomUUID(),
      target,
      researcher_id: await authenticatedActorKey(),
      note: null,
      occurred_at: new Date().toISOString(),
    },
  ),
  recordProductReview: async (
    enrollmentId: string,
    input: { decision: "continue" | "suspend" | "retire" | "replace"; reason: string },
  ) => postJson<ProductReviewResponse>(
    `/api/v2/products/${enrollmentId}/reviews`,
    {
      idempotency_key: crypto.randomUUID(),
      decision: input.decision,
      researcher_id: await authenticatedActorKey(),
      reason: input.reason,
      evidence: {},
      reviewed_at: new Date().toISOString(),
    },
  ),
  dataRequirements: () =>
    getJson<DataRequirementResponse>("/api/v2/catalog/data-requirements"),
  dataOverview: () => getJson<DataOverviewResponse>("/api/v2/data/overview"),
  factorOverview: () => getJson<FactorOverviewResponse>("/api/v2/factors/overview"),
  signalOverview: (frequency: "weekly" | "monthly") =>
    getJson<SignalOverviewResponse>(`/api/v2/signals/overview?frequency=${frequency}`),
  modelOverview: (frequency: "weekly" | "monthly") =>
    getJson<ModelOverviewResponse>(`/api/v2/models/overview?frequency=${frequency}`),
  strategyOverview: () =>
    getJson<StrategyOverviewResponse>("/api/v2/strategies/overview"),
  strategyTargetPath: (artifactId: string) =>
    getJson<StrategyTargetPathResponse>(`/api/v2/strategies/targets/${artifactId}`),
  experimentOverview: (filters: {
    researchSuiteId?: string;
    status?: string;
    templateKey?: string;
    frequency?: "weekly" | "monthly";
    costBpsPerSide?: number;
    rankingMetric?: string;
    limit?: number;
    offset?: number;
  } = {}) => {
    const search = new URLSearchParams();
    if (filters.researchSuiteId) search.set("research_suite_id", filters.researchSuiteId);
    if (filters.status && filters.status !== "all") search.set("status", filters.status);
    if (filters.templateKey) search.set("template_key", filters.templateKey);
    if (filters.frequency) search.set("frequency", filters.frequency);
    if (filters.costBpsPerSide !== undefined) search.set("cost_bps_per_side", String(filters.costBpsPerSide));
    if (filters.rankingMetric) search.set("ranking_metric", filters.rankingMetric);
    if (filters.limit !== undefined) search.set("limit", String(filters.limit));
    if (filters.offset !== undefined) search.set("offset", String(filters.offset));
    const suffix = search.size ? `?${search.toString()}` : "";
    return getJson<ExperimentOverviewResponse>(`/api/v2/experiments/overview${suffix}`);
  },
  experimentResult: (artifactId: string) =>
    getJson<ExperimentResultResponse>(`/api/v2/experiments/results/${artifactId}`),
  promotionQualification: (artifactId: string) =>
    getJson<PromotionQualificationResponse>(
      `/api/v2/experiments/results/${artifactId}/qualification`,
    ),
  promoteResult: async (artifactId: string, input: {
    name: string;
    selectionReason: string;
    note?: string;
  }) => postJson<ProductPromotionResponse>(
    `/api/v2/experiments/results/${artifactId}/promote`,
    {
      idempotency_key: crypto.randomUUID(),
      name: input.name,
      researcher_id: await authenticatedActorKey(),
      selection_reason: input.selectionReason,
      note: input.note || null,
    },
  ),
  productRanking: (metric: string, cohortArtifactId = "") => {
    const search = new URLSearchParams({ metric });
    if (cohortArtifactId) search.set("cohort_artifact_id", cohortArtifactId);
    return getJson<ProductRankingResponse>(`/api/v2/rankings/products?${search.toString()}`);
  },
  productCompare: (resultArtifactIds: string[]) => {
    const search = new URLSearchParams();
    resultArtifactIds.forEach((item) => search.append("result_artifact_id", item));
    return getJson<ProductCompareResponse>(`/api/v2/compare/products?${search.toString()}`);
  },
  decisionExplorer: (resultArtifactId: string, decisionDate = "") => {
    const search = new URLSearchParams();
    if (decisionDate) search.set("decision_date", decisionDate);
    const suffix = search.size ? `?${search.toString()}` : "";
    return getJson<DecisionExplorerResponse>(
      `/api/v2/experiments/results/${resultArtifactId}/decisions${suffix}`,
    );
  },
  artifacts: (statuses = ["published"]) => {
    const search = new URLSearchParams();
    statuses.forEach((status) => search.append("status", status));
    search.set("limit", "100");
    return getJson<ArtifactListResponse>(`/api/v2/artifacts?${search.toString()}`);
  },
  artifact: (artifactId: string) =>
    getJson<ArtifactDetailResponse>(`/api/v2/artifacts/${artifactId}`),
  lineage: (artifactId: string) =>
    getJson<LineageManifestResponse>(`/api/v2/artifacts/${artifactId}/lineage`),
};
