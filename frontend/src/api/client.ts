import type { components } from "./schema.generated";

export type HealthResponse = components["schemas"]["HealthResponse"];
export type CapabilitiesResponse = components["schemas"]["CapabilitiesResponse"];
export type ArtifactListResponse = components["schemas"]["ArtifactListResponse"];
export type ArtifactDetailResponse = components["schemas"]["ArtifactDetailResponse"];
export type LineageManifestResponse = components["schemas"]["LineageManifestResponse"];
export type AssetCatalogResponse = components["schemas"]["AssetCatalogResponse"];
export type AssetSeriesResponse = components["schemas"]["AssetSeriesResponse"];
export type WorkspaceOptionsResponse = components["schemas"]["WorkspaceOptionsResponse"];
export type WorkspaceCompilePreviewResponse = components["schemas"]["WorkspaceCompilePreviewResponse"];
export type WorkspaceDraftResponse = components["schemas"]["WorkspaceDraftResponse"];
export type ReleaseGateResponse = components["schemas"]["ReleaseGateResponse"];
export type WorkspaceSuiteSubmitResponse = components["schemas"]["WorkspaceSuiteSubmitResponse"];
export type WorkspaceSuiteStatusResponse = components["schemas"]["WorkspaceSuiteStatusResponse"];
export type WorkspaceSuiteCancelResponse = components["schemas"]["WorkspaceSuiteCancelResponse"];
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

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { message?: string };
      if (payload.message) message = payload.message;
    } catch {
      // A non-JSON proxy error still retains the status-based message.
    }
    throw new ApiClientError(message, response.status);
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
    const payload = await response.json().catch(() => ({})) as { message?: string };
    throw new ApiClientError(payload.message ?? `${response.status} ${response.statusText}`, response.status);
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
    const payload = await response.json().catch(() => ({})) as { message?: string };
    throw new ApiClientError(payload.message ?? `${response.status} ${response.statusText}`, response.status);
  }
  return await response.json() as T;
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
  workspaceDraft: (researcherId = "local", draftKey = "default") =>
    getJson<WorkspaceDraftResponse>(
      `/api/v2/workspace/drafts/${encodeURIComponent(researcherId)}/${encodeURIComponent(draftKey)}`,
    ),
  saveWorkspaceDraft: (input: {
    researcherId?: string;
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
  }) => {
    const researcherId = input.researcherId ?? "local";
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
  },
  releaseGates: () => getJson<ReleaseGateResponse>("/api/v2/release-gates"),
  submitWorkspaceSuite: (
    expectedRevision: number,
    suiteMode: "formal" | "exploratory" = "exploratory",
  ) =>
    postJson<WorkspaceSuiteSubmitResponse>("/api/v2/workspace/suites", {
      idempotency_key: crypto.randomUUID(),
      researcher_id: "local",
      draft_key: "default",
      expected_revision: expectedRevision,
      suite_mode: suiteMode,
    }),
  workspaceSuiteStatus: (suiteId: string) =>
    getJson<WorkspaceSuiteStatusResponse>(`/api/v2/workspace/suites/${suiteId}`),
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
  productDetail: (enrollmentId: string) =>
    getJson<ProductDetailResponse>(`/api/v2/products/${enrollmentId}`),
  productRecommendation: (enrollmentId: string) =>
    getJson<ProductRecommendationResponse>(`/api/v2/products/${enrollmentId}/recommendation`),
  changeProductLifecycle: (enrollmentId: string, input: {
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
      researcher_id: "local",
      requested_at: new Date().toISOString(),
      effective_at: input.effectiveAt,
    },
  ),
  changeProductAlert: (
    alertId: string,
    target: "acknowledged" | "resolved" | "superseded",
  ) => postJson<ProductAlertChangeResponse>(
    `/api/v2/products/alerts/${alertId}/status`,
    {
      idempotency_key: crypto.randomUUID(),
      target,
      researcher_id: "local",
      note: null,
      occurred_at: new Date().toISOString(),
    },
  ),
  recordProductReview: (
    enrollmentId: string,
    input: { decision: "continue" | "suspend" | "retire" | "replace"; reason: string },
  ) => postJson<ProductReviewResponse>(
    `/api/v2/products/${enrollmentId}/reviews`,
    {
      idempotency_key: crypto.randomUUID(),
      decision: input.decision,
      researcher_id: "local",
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
  promoteResult: (artifactId: string, input: {
    name: string;
    selectionReason: string;
    note?: string;
  }) => postJson<ProductPromotionResponse>(
    `/api/v2/experiments/results/${artifactId}/promote`,
    {
      idempotency_key: crypto.randomUUID(),
      name: input.name,
      researcher_id: "local",
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
