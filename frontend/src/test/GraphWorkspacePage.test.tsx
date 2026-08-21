import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  ApiClientError,
  type AssetCatalogResponse,
  type GraphDraftSnapshotResponse,
  type GraphStageFamilyPageResponse,
  type GraphWorkspacePreviewResponse,
} from "../api/client";
import i18n from "../i18n";
import { GraphWorkspacePage } from "../pages/GraphWorkspacePage";
import { GraphDraftProvider, useGraphDraft } from "../workspace/GraphDraftContext";

function QueueHarness({ prefix = "" }: { prefix?: string }) {
  const graph = useGraphDraft();
  return <div>
    <button type="button" disabled={!graph.snapshot} onClick={() => void graph.toggleFeature("queue_first", 3, false)}>{prefix}Queue first</button>
    <button type="button" disabled={!graph.snapshot} onClick={() => void graph.toggleFeature("queue_second", 3, false)}>{prefix}Queue second</button>
    <output aria-label={`${prefix}pending commands`}>{graph.pendingCommandCount}</output>
    <output aria-label={`${prefix}queue state`}>{graph.queuePaused ? "paused" : "running"}</output>
    <output aria-label={`${prefix}revision`}>{graph.snapshot?.revision ?? 0}</output>
    <button type="button" onClick={() => void graph.reload()}>{prefix}Resume</button>
  </div>;
}

function GraphRouteHarness() {
  return <GraphDraftProvider>
    <nav>
      <Link to="/context">Assets</Link>
      <Link to="/processing-1">Layer 1</Link>
      <Link to="/processing-2">Layer 2</Link>
      <Link to="/processing-3">Layer 3</Link>
      <Link to="/aggregation">Aggregation</Link>
    </nav>
    <Outlet />
  </GraphDraftProvider>;
}

class TestBroadcastChannel {
  static readonly channels = new Set<TestBroadcastChannel>();
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(readonly name: string) {
    TestBroadcastChannel.channels.add(this);
  }

  postMessage(data: unknown) {
    for (const channel of TestBroadcastChannel.channels) {
      if (channel !== this && channel.name === this.name) {
        queueMicrotask(() => channel.onmessage?.(new MessageEvent("message", { data })));
      }
    }
  }

  close() {
    TestBroadcastChannel.channels.delete(this);
  }
}

interface StrategyPresetFixture {
  preset_key: string;
  name: string;
  description: string;
  version_number: number;
  parameters: Record<string, unknown>;
  selected: boolean;
  selectable: boolean;
  reason_codes: string[];
}

type GraphWorkspacePreviewFixture = Omit<GraphWorkspacePreviewResponse, "strategies"> & {
  strategies: Array<GraphWorkspacePreviewResponse["strategies"][number] & {
    parameter_presets: StrategyPresetFixture[];
  }>;
};

function derivedView(
  selected: boolean,
  selectedInvalidStrategyPreset = false,
  selectedDefense: "none" | "fixed20_defense" | "ma200_tiered_defense" = "none",
): GraphWorkspacePreviewFixture {
  const emptyStage = (stage_no: 0 | 1 | 2) => ({
    stage_no,
    explicit_count: 0,
    required_count: selected ? 1 : 0,
    families: [],
  });
  return {
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: selected ? "ok" : "warning", codes: [] },
    catalog_release: {
      release_key: "bird_v022_catalog",
      catalog_version: "0.22.1",
      contract_version: "v0.22.0",
      source_manifest_hash: "a".repeat(64),
    },
    selection_fingerprint: "b".repeat(64),
    derived_state_fingerprint: "c".repeat(64),
    frequency: "weekly",
    summary: {
      explicit_count: selected ? 1 : 0,
      required_count: selected ? 3 : 0,
      stage3_input_count: selected ? 1 : 0,
      aggregation_instance_count: selected ? 1 : 0,
      strategy_branch_count: selected ? 1 : 0,
      backtest_cell_count: selected ? 7 : 0,
    },
    aggregation_inputs: selected ? ["return_continuation__w120"] : [],
    aggregations: [{
      family_key: "flat_equal_weight_mean",
      name: "Flat equal-weight mean",
      algorithm_identity: "Q18(sum(x_i/n)) in explicit input order",
      objective_semantics: { type: "deterministic_mean" },
      output_semantics: { direction: "higher_is_better" },
      execution_mode: "deterministic",
      input_payload_contract_key: "final_signal_numeric",
      output_payload_contract_key: "final_signal_numeric",
      ordering_policy: "explicit_input_order",
      input_policy: { stage: 3, all_inputs_consumed: true },
      compatibility_policy: { pit_required: true },
      missing_policy: { mode: "published_complete_case_policy" },
      tie_policy: { mode: "preserve_numeric_ties" },
      selected: selectedDefense === "none",
      minimum_inputs: 1,
      maximum_inputs: 128,
      parameter_presets: ["signal_equal_v1"],
      parameter_preset_definitions: [{
        preset_key: "signal_equal_v1", name: "Signal equal weight",
        description: "Every direct signal receives equal weight.", version_number: 1,
        semantics: { weight_policy: "equal_direct_inputs" }, selected: true,
        selectable: true, reason_codes: [],
      }],
      selected_parameter_presets: ["signal_equal_v1"],
      internal_member_count: 0,
      accepted_input_count: selected ? 1 : 0,
    }],
    strategies: [{
      family_key: "cross_section_rank_top_k",
      variant_key: "cross_section_rank_top_k_parity",
      name: "Cross-section rank Top-K",
      selection_semantics: { ranking: "descending_higher_is_better", selection: "top_k" },
      research_hypothesis: "A directionally stable cross-sectional score can select the strongest assets at each planned rebalance.",
      parameters: { allowed_k: [1, 2, 3] },
      input_payload_contract_key: "final_signal_numeric",
      schedule_policy: { frequencies: ["weekly", "monthly"] },
      execution_policy: { long_only: true, weighting: "equal_selected_assets" },
      supported_frequencies: ["weekly", "monthly"],
      selected: true,
      compatible: true,
      reason_codes: [],
      parameter_presets: [{
        preset_key: "k1",
        name: "ETF Top 1",
        description: "Hold the highest-ranked ETF.",
        version_number: 1,
        parameters: { target_k: 1, selection_buffer: "none", sector_cap: "none" },
        selected: false,
        selectable: true,
        reason_codes: [],
      }, {
        preset_key: "k2",
        name: "ETF Top 2",
        description: "Hold the two highest-ranked ETFs.",
        version_number: 1,
        parameters: { target_k: 2, selection_buffer: "none", sector_cap: "none" },
        selected: true,
        selectable: true,
        reason_codes: [],
      }, {
        preset_key: "k3",
        name: "ETF Top 3",
        description: "Hold the three highest-ranked ETFs.",
        version_number: 1,
        parameters: { target_k: 3, selection_buffer: "none", sector_cap: "none" },
        selected: false,
        selectable: true,
        reason_codes: [],
      }],
    }, {
      family_key: "cross_section_rank_top_k",
      variant_key: "cross_section_rank_top_k_large_cap_parity",
      name: "Cross-section rank Top-K",
      selection_semantics: { ranking: "descending_higher_is_better", selection: "top_k" },
      research_hypothesis: "A directionally stable cross-sectional score can select the strongest assets at each planned rebalance.",
      parameters: { allowed_k: [10, 20] },
      input_payload_contract_key: "final_signal_numeric",
      schedule_policy: { frequencies: ["weekly"] },
      execution_policy: { long_only: true, weighting: "equal_selected_assets" },
      supported_frequencies: ["weekly"],
      selected: selectedInvalidStrategyPreset,
      compatible: true,
      reason_codes: [],
      parameter_presets: [{
        preset_key: "k10",
        name: "Large-cap Top 10",
        description: "Hold the ten highest-ranked large-cap stocks.",
        version_number: 1,
        parameters: { target_k: 10, selection_buffer: "none", sector_cap: "none" },
        selected: selectedInvalidStrategyPreset,
        selectable: false,
        reason_codes: ["insufficient_eligible_assets"],
      }, {
        preset_key: "k20",
        name: "Large-cap Top 20",
        description: "Hold the twenty highest-ranked large-cap stocks.",
        version_number: 1,
        parameters: { target_k: 20, selection_buffer: "none", sector_cap: "none" },
        selected: false,
        selectable: false,
        reason_codes: ["insufficient_eligible_assets"],
      }],
    }],
    defenses: [{
      family_key: "none",
      variant_key: "none",
      name: "No defense",
      allocation_semantics: { risk_budget: "1", defense_budget: "0" },
      research_hypothesis: "No defensive sleeve is applied.",
      parameters: {},
      input_policy: {},
      allocation_policy_document: {},
      supported_asset_context_keys: [],
      selected: true,
      compatible: true,
      composed: false,
    }, {
      family_key: "fixed_defensive_allocation",
      variant_key: "fixed20_defense",
      name: "Fixed 20% defense",
      allocation_semantics: { risk_budget: "0.8", defense_budget: "0.2" },
      research_hypothesis: "A fixed defensive sleeve provides a transparent parity baseline.",
      parameters: { defense_budget: 0.2 },
      input_policy: {},
      allocation_policy_document: {},
      supported_asset_context_keys: ["us_style_rotation_4_etf_sample_v1"],
      selected: selectedDefense === "fixed20_defense",
      compatible: true,
      composed: true,
      version_number: 3,
      research_status: "parity",
      timing_policy: {
        family_key: "fixed_defense_budget",
        variant_key: "fixed20_budget",
        name: "Fixed defensive budget",
        formula_identity: "defense_budget_equals_published_constant",
        research_hypothesis: "A fixed defensive budget provides a transparent parity baseline without making a market-timing claim.",
        version_number: 1,
        research_status: "parity",
        supported_frequencies: ["weekly", "monthly"],
        input_policy: { market_timing_signal_required: false },
        rule: {
          rule_type: "fixed_budget",
          budget: { risk_budget: "0.800000000000000000", defense_budget: "0.200000000000000000" },
        },
      },
      allocation_policy: {
        family_key: "standard_fixed_defensive_basket",
        variant_key: "standard_defensive_basket_long_history_v1",
        name: "Standard fixed defensive basket",
        formula_identity: "published_member_target_weights_sum_to_one",
        research_hypothesis: "The frozen v0.21 long-history defensive basket provides a parity allocation for researching a defensive sleeve.",
        version_number: 1,
        asset_registry_catalog_version: "0.21.1",
        asset_set_key: "standard_defensive_basket_long_history_v1",
        research_status: "exploratory",
        formal_eligible: false,
        missing_member_policy: "fail",
        reserve_fallback_policy: "forbidden",
        rebalance_policy: "with_strategy",
        reserve_return_model: { model_key: "dgs3mo_cash_accrual_proxy", version_number: 1 },
        members: [
          { ordinal: 0, asset_key: "synthetic_reserve", component_role: "reserve", sleeve_weight: "0.400000000000000000" },
          { ordinal: 1, asset_key: "ief", component_role: "defensive_asset", sleeve_weight: "0.250000000000000000" },
          { ordinal: 2, asset_key: "tlt", component_role: "defensive_asset", sleeve_weight: "0.100000000000000000" },
          { ordinal: 3, asset_key: "tip", component_role: "defensive_asset", sleeve_weight: "0.150000000000000000" },
          { ordinal: 4, asset_key: "iau", component_role: "defensive_asset", sleeve_weight: "0.100000000000000000" },
        ],
      },
    }, {
      family_key: "ma200_tiered_regime_defense",
      variant_key: "ma200_tiered_defense",
      name: "SPY MA200 tiered regime defense",
      allocation_semantics: {},
      research_hypothesis: "The frozen v0.21 SPY MA200 tiers provide an explicit defensive parity baseline.",
      parameters: { window: 200 },
      input_policy: {},
      allocation_policy_document: {},
      supported_asset_context_keys: ["us_style_rotation_4_etf_sample_v1"],
      selected: selectedDefense === "ma200_tiered_defense",
      compatible: true,
      composed: true,
      version_number: 2,
      research_status: "parity",
      timing_policy: {
        family_key: "market_moving_average_tiered_budget",
        variant_key: "spy_ma200_tiered_budget",
        name: "Market moving-average tiered budget",
        formula_identity: "market_adjusted_close_div_session_sma_minus_one_to_published_budget_tiers",
        research_hypothesis: "The frozen v0.21 SPY MA200 tiers provide an explicit market-regime parity baseline.",
        version_number: 1,
        research_status: "parity",
        supported_frequencies: ["weekly", "monthly"],
        input_policy: { market_timing_signal_required: true, known_at_required: true, missing_input: "fail" },
        rule: {
          rule_type: "moving_average_tiered_budget",
          reference_asset_key: "spy",
          price_field: "adjusted_close",
          moving_average_window_sessions: 200,
          indicator_key: "spy_close_div_sma200_minus_one",
          upper_threshold: "0.020000000000000000",
          lower_threshold: "-0.020000000000000000",
          boundary_policy: "strict_outer_inclusive_middle",
        },
      },
      allocation_policy: {
        family_key: "standard_fixed_defensive_basket",
        variant_key: "standard_defensive_basket_long_history_v1",
        name: "Standard fixed defensive basket",
        formula_identity: "standard_defensive_basket_long_history_v1",
        research_hypothesis: "The frozen v0.21 long-history defensive basket provides a parity allocation for researching a defensive sleeve.",
        version_number: 1,
        asset_registry_catalog_version: "0.21.1",
        asset_set_key: "standard_defensive_basket_long_history_v1",
        research_status: "exploratory",
        formal_eligible: false,
        missing_member_policy: "fail",
        reserve_fallback_policy: "forbidden",
        rebalance_policy: "with_strategy",
        reserve_return_model: { model_key: "dgs3mo_cash_accrual_proxy", version_number: 1 },
        members: [
          { ordinal: 0, asset_key: "synthetic_reserve", component_role: "reserve", sleeve_weight: "0.400000000000000000" },
          { ordinal: 1, asset_key: "ief", component_role: "defensive_asset", sleeve_weight: "0.250000000000000000" },
          { ordinal: 2, asset_key: "tlt", component_role: "defensive_asset", sleeve_weight: "0.100000000000000000" },
          { ordinal: 3, asset_key: "tip", component_role: "defensive_asset", sleeve_weight: "0.150000000000000000" },
          { ordinal: 4, asset_key: "iau", component_role: "defensive_asset", sleeve_weight: "0.100000000000000000" },
        ],
      },
    }],
    stages: [
      emptyStage(0),
      emptyStage(1),
      emptyStage(2),
      {
        stage_no: 3,
        explicit_count: selected ? 1 : 0,
        required_count: 0,
        families: [{
          family_key: "return_continuation",
          name: "Return continuation",
          pinned: selected,
          explicit_count: selected ? 1 : 0,
          required_count: 0,
          available_count: 1,
          variants: [{
            family_key: "return_continuation",
            feature_key: "return_continuation__w120",
            name: "Return continuation",
            stage_no: 3,
            origin_stage: 3,
            formula_identity: "directional_cross_sectional_centered_rank_q18",
            semantic_role: "momentum_signal",
            unit: "centered_rank",
            parameters: { window: 120 },
            input_feature_keys: ["total_return__w120"],
            output_semantics: { continuous: true, rank_meaning: true },
            payload_contract_key: "final_signal_numeric",
            direction: "higher_is_better",
            aggregation_readiness: "aggregation_ready",
            research_hypothesis: "Past total return captures continuation.",
            is_explicit: selected,
            is_required: false,
            is_present: selected,
            required_by: [],
            availability: "requires_ancestors",
            lock_state: "unlocked",
            locked_by: [],
            pinned: selected,
            producer: {
              kind: "node_output",
              node_variant_key: "return_continuation_node__w120",
              output_port_key: "signal_score",
            },
            select_effect: { ancestor_count: 3, projection_count: 1 },
            reason_codes: [],
          }],
        }],
      },
    ],
    blockers: selected ? [] : [{
      layer: "stage3",
      object_key: "aggregation_inputs",
      reason_codes: ["stage3_input_required"],
      feature_keys: [],
    }],
    warnings: [],
    resources: {
      policy_id: "v022-m0-policy-v0.22.0",
      state: "accepted",
      estimates: {
        explicit_stage3_inputs: selected ? 1 : 0,
        feature_occurrences: selected ? 4 : 0,
        ancestor_occurrences: selected ? 3 : 0,
        graph_edges: selected ? 3 : 0,
        aggregation_candidates: 1,
        aggregation_instances: selected ? 1 : 0,
        strategy_candidates: 1,
        defense_candidates: 1,
        strategy_branches: selected ? 1 : 0,
        backtest_cells: selected ? 7 : 0,
        work_items: selected ? 7 : 0,
      },
      checks: [],
      reason_codes: [],
    },
  };
}

function snapshot(
  selected: boolean,
  selectedInvalidStrategyPreset = false,
  selectedDefense: "none" | "fixed20_defense" | "ma200_tiered_defense" = "none",
): GraphDraftSnapshotResponse {
  const view = derivedView(selected, selectedInvalidStrategyPreset, selectedDefense);
  return {
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: selected ? "ok" : "warning", codes: [] },
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    catalog_release_id: "22222222-2222-4222-8222-222222222222",
    draft_key: "browser_default_v1",
    name: "v0.22 Graph Workspace",
    status: "draft",
    revision: selected ? 2 : 1,
    intent: {
      frequency: "weekly",
      aggregation_family_keys: ["flat_equal_weight_mean"],
      aggregation_parameter_preset_keys: {
        flat_equal_weight_mean: ["signal_equal_v1"],
      },
      strategy_keys: ["cross_section_rank_top_k_parity"],
      strategy_parameter_preset_keys: {
        cross_section_rank_top_k_parity: ["k2"],
        ...(selectedInvalidStrategyPreset
          ? { cross_section_rank_top_k_large_cap_parity: ["k10"] }
          : {}),
      },
      defense_keys: [selectedDefense],
      explicit_features: selected
        ? [{ feature_key: "return_continuation__w120", stage_no: 3 }]
        : [],
    },
    asset_context: {
      asset_context_key: "us_style_rotation_4_etf_sample_v1",
      selection_kind: "fixed_asset_set",
      members: [
        { ordinal: 0, security_id: "00000000-0000-4000-8000-000000000011", security_key: "iwf", instrument_type: "Equity ETF" },
        { ordinal: 1, security_id: "00000000-0000-4000-8000-000000000012", security_key: "iwd", instrument_type: "Equity ETF" },
      ],
    },
    resolved_data_binding: { data_input_keys: ["canonical_market_bars"] },
    derived_view: view,
    applied: true,
  };
}

let selectedState = false;

function rawInputFamily(): GraphStageFamilyPageResponse["catalog_families"][number] {
  return {
    family_key: "adjusted_close",
    name: "Total-return adjusted close",
    pinned: false,
    explicit_count: 0,
    required_count: 0,
    available_count: 1,
    variants: [{
      family_key: "adjusted_close",
      feature_key: "adjusted_close",
      name: "Total-return adjusted close",
      stage_no: 1,
      origin_stage: 0,
      formula_identity: "vendor total-return adjusted close",
      semantic_role: "adjusted_market_close",
      unit: "price",
      parameters: {},
      input_feature_keys: [],
      output_semantics: {
        source_series_key: "us_etf_daily_market",
        source_field: "adj_close",
      },
      payload_contract_key: "market_price_scalar",
      direction: "not_applicable",
      aggregation_readiness: "not_aggregation_ready",
      research_hypothesis: "Adjusted close is the frozen price basis for legacy return calculations.",
      is_explicit: false,
      is_required: false,
      is_present: false,
      required_by: [],
      availability: "ready",
      lock_state: "unlocked",
      locked_by: [],
      pinned: false,
      producer: {
        kind: "layer_projection",
        source_feature_key: "adjusted_close",
        source_stage_no: 0,
      },
      select_effect: { ancestor_count: 1, projection_count: 1 },
      reason_codes: [],
    }],
  };
}

function familyPage(stageNo: 0 | 1 | 2 | 3): GraphStageFamilyPageResponse {
  const stage = derivedView(selectedState).stages[stageNo];
  const catalogFamilies = stageNo === 1
    ? [rawInputFamily(), ...stage.families.filter((family) => !family.pinned)]
    : stage.families.filter((family) => !family.pinned);
  return {
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    revision: selectedState ? 2 : 1,
    stage_no: stageNo,
    view_token: selectedState ? "d".repeat(64) : "e".repeat(64),
    pinned_families: stage.families.filter((family) => family.pinned),
    catalog_families: catalogFamilies,
    next_cursor: null,
    total_catalog_family_count: catalogFamilies.length,
  };
}

beforeEach(async () => {
  vi.unstubAllGlobals();
  TestBroadcastChannel.channels.clear();
  selectedState = false;
  window.localStorage.clear();
  vi.restoreAllMocks();
  await i18n.changeLanguage("zh-CN");
  vi.spyOn(api, "graphDraftByKey").mockRejectedValue(
    new ApiClientError("Graph Draft not found", 404, "not_found"),
  );
  vi.spyOn(api, "currentGraphDraftCompile").mockRejectedValue(
    new ApiClientError("Current compile not found", 404, "current_compile_not_found"),
  );
  vi.spyOn(api, "graphSuiteRuntimeReadiness").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    ready: true,
    state: "ready",
    worker_key: "test-suite-worker",
    process_id: 1234,
    heartbeat_at: "2026-08-14T00:00:00Z",
    max_age_seconds: 10,
  });
  vi.spyOn(api, "createGraphDraft").mockResolvedValue(snapshot(false));
  vi.spyOn(api, "applyGraphDraftEvent").mockImplementation(async () => {
    selectedState = true;
    return snapshot(true);
  });
  vi.spyOn(api, "graphStageFamilies").mockImplementation(
    async (_draftId, input) => familyPage(input.stageNo),
  );
});

test("a clean browser restores the server Draft by stable key before creating", async () => {
  vi.mocked(api.graphDraftByKey).mockResolvedValue(snapshot(true));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view={1} /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { level: 1, name: "加工层 1" })).toBeInTheDocument();
  expect(api.graphDraftByKey).toHaveBeenCalledWith("browser_default_v1");
  expect(api.createGraphDraft).not.toHaveBeenCalled();
  expect(JSON.parse(window.localStorage.getItem("style-rotation-v022-graph-draft") ?? "{}"))
    .toMatchObject({ graphDraftId: "11111111-1111-4111-8111-111111111111" });
});

test("a clean browser restores the current compile from the server", async () => {
  const restored = snapshot(true);
  const compiled = {
    context: restored.context,
    quality: { state: "ok" as const, codes: [] },
    graph_draft_id: restored.graph_draft_id,
    graph_draft_revision: restored.revision,
    draft_intent_id: "22222222-2222-4222-8222-222222222222",
    compile_attempt_id: "33333333-3333-4333-8333-333333333333",
    compiled_research_graph_id: "44444444-4444-4444-8444-444444444444",
    graph_artifact_id: "55555555-5555-4555-8555-555555555555",
    graph_fingerprint: "a".repeat(64),
    reused: true,
    compiled_execution_data_context_id: "66666666-6666-4666-8666-666666666666",
    execution_data_context_artifact_id: "77777777-7777-4777-8777-777777777777",
    execution_data_context_fingerprint: "b".repeat(64),
    execution_data_context_reused: true,
    defense_execution_contexts: [],
    selection_fingerprint: restored.derived_view.selection_fingerprint,
  };
  vi.mocked(api.graphDraftByKey).mockResolvedValue(restored);
  vi.mocked(api.currentGraphDraftCompile).mockResolvedValue(compiled);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  await waitFor(() => expect(api.currentGraphDraftCompile).toHaveBeenCalledWith(
    restored.graph_draft_id,
  ));
  await waitFor(() => expect(
    JSON.parse(window.sessionStorage.getItem("style-rotation-v022-last-compile") ?? "null"),
  ).toMatchObject({ compiled_research_graph_id: compiled.compiled_research_graph_id }));
});

function universeAssetCatalog(): AssetCatalogResponse {
  const item = (
    securityId: string,
    symbol: string,
    name: string,
    instrumentType: string,
    categoryKey: string,
    selectable = true,
    v022CandidateSelectable = selectable,
  ): AssetCatalogResponse["items"][number] => ({
    security_id: securityId,
    asset_id: securityId,
    asset_key: symbol.toLocaleLowerCase(),
    name,
    category_key: categoryKey,
    asset_class: categoryKey,
    instrument_type: instrumentType,
    status: "active",
    symbol,
    aliases: [],
    venue_mic: "XNYS",
    currency: "USD",
    calendar_key: "XNYS",
    tradability: selectable ? "tradable" : "reference_only",
    tags: [],
    maturity: "research_ready",
    target_maturity: "research_ready",
    missing_requirements: [],
    canonical_data_available: true,
    selectable,
    v022_candidate_selectable: v022CandidateSelectable,
    v022_candidate_reason_codes: v022CandidateSelectable
      ? []
      : ["outside_frozen_candidate_dataset"],
    v022_candidate_dataset_key: v022CandidateSelectable
      ? (categoryKey === "stocks"
        ? "us_sp500_historical_daily_free_research_v1"
        : "us_etf_daily_market_canonical")
      : null,
    v022_candidate_dataset_version: v022CandidateSelectable ? 1 : null,
    data_inputs: [],
  });
  const items = [
    item("00000000-0000-4000-8000-000000000011", "IWF", "iShares Russell 1000 Growth", "Equity ETF", "equity_etfs"),
    item("00000000-0000-4000-8000-000000000012", "IWD", "iShares Russell 1000 Value", "Equity ETF", "equity_etfs"),
    item("00000000-0000-4000-8000-000000000021", "AAPL", "Apple", "Common Stock", "stocks"),
    item("00000000-0000-4000-8000-000000000022", "MSFT", "Microsoft", "Common Stock", "stocks"),
    item("00000000-0000-4000-8000-000000000023", "ALNY", "Alnylam", "Common Stock", "stocks", true, false),
    item("00000000-0000-4000-8000-000000000031", "SPX", "S&P 500 Index", "Index", "indices", false),
  ];
  return {
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    release_artifact_id: "00000000-0000-4000-8000-000000000099",
    release_version_number: 1,
    catalog_version: "0.21.1",
    as_of_date: "2026-08-13",
    total: items.length,
    limit: items.length,
    offset: 0,
    categories: [],
    asset_sets: [],
    items,
  };
}

function snapshotWithAssetSelection(securityIds: string[]): GraphDraftSnapshotResponse {
  const next = snapshot(false);
  const catalog = universeAssetCatalog();
  const byId = new Map(catalog.items.map((item) => [item.security_id, item]));
  next.revision = 2;
  next.asset_context = {
    asset_context_key: "explicit_test_selection",
    selection_kind: "explicit_security_selection",
    members: securityIds.map((securityId, ordinal) => {
      const item = byId.get(securityId);
      if (!item) throw new Error(`Missing test asset ${securityId}`);
      return {
        ordinal,
        security_id: securityId,
        security_key: item.asset_key,
        instrument_type: item.instrument_type,
      };
    }),
  };
  return next;
}

test("Universe Builder preserves the exact saved assets across downstream navigation", async () => {
  vi.spyOn(api, "allAssets").mockResolvedValue(universeAssetCatalog());
  const expectedSecurityIds = [
    "00000000-0000-4000-8000-000000000021",
    "00000000-0000-4000-8000-000000000022",
  ];
  vi.mocked(api.applyGraphDraftEvent).mockResolvedValueOnce(
    snapshotWithAssetSelection(expectedSecurityIds),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/context"]}>
        <Routes>
          <Route element={<GraphRouteHarness />}>
            <Route path="context" element={<GraphWorkspacePage view="context" />} />
            <Route path="processing-1" element={<GraphWorkspacePage view={1} />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "选择参与信号排名与持仓构建的资产" })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("checkbox", { name: /IWF/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /IWD/ }));
  fireEvent.click(screen.getByRole("button", { name: "股票 / ADR" }));
  expect(screen.getByRole("checkbox", { name: /ALNY/ })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "全选当前共同数据集" }));
  expect(screen.getByRole("button", { name: "导出全部已选资产数据" })).toBeDisabled();
  expect(screen.getByText("请先保存资产选择")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存资产选择" }));

  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    {
      expectedRevision: 1,
      eventType: "set_asset_selection",
      event: {
        security_ids: expectedSecurityIds,
      },
    },
  ));
  expect(await screen.findByText("资产选择已保存到版本 2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出全部已选资产数据" })).toBeEnabled();

  fireEvent.click(screen.getByRole("link", { name: "Layer 1" }));
  expect(await screen.findByRole("heading", { level: 1, name: "加工层 1" })).toBeInTheDocument();
  expect(screen.getByText("候选资产").parentElement).toHaveTextContent("2");
  expect(api.applyGraphDraftEvent).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("link", { name: "Assets" }));
  expect(await screen.findByRole("checkbox", { name: /AAPL/ })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /MSFT/ })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "ETF / ETP" }));
  expect(screen.getByRole("checkbox", { name: /IWF/ })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: /IWD/ })).not.toBeChecked();
  expect(api.applyGraphDraftEvent).toHaveBeenCalledTimes(1);
});

test("saved Universe exposes a top-level full export action and reuses the preview request", async () => {
  const saved = snapshotWithAssetSelection([
    "00000000-0000-4000-8000-000000000021",
    "00000000-0000-4000-8000-000000000022",
  ]);
  vi.mocked(api.graphDraftByKey).mockResolvedValue(saved);
  vi.spyOn(api, "allAssets").mockResolvedValue(universeAssetCatalog());
  vi.spyOn(api, "previewAssetDataExport").mockResolvedValue({
    context: saved.context,
    quality: { state: "warning" as const, codes: ["free_source_retrospective_prices"] },
    graph_draft_id: saved.graph_draft_id,
    graph_draft_revision: 2,
    asset_registry_release_id: "00000000-0000-4000-8000-000000000091",
    dataset_publication_id: "00000000-0000-4000-8000-000000000092",
    dataset_gate_assessment_id: "00000000-0000-4000-8000-000000000093",
    dataset_key: "us_sp500_free_research_frozen_v5_baseline",
    dataset_version_number: 1,
    price_semantics: "split_normalized_ohlcv_dividends_backward_total_return_v2",
    asset_count: 2,
    start_date: "2007-01-03",
    end_date: "2026-06-30",
    row_count: 9806,
    estimated_bytes: 549136,
    export_format: "parquet",
    fields: ["close_adj"],
    warning_codes: ["free_source_retrospective_prices"],
    request_fingerprint: "a".repeat(64),
  });
  const completedJob = {
    context: saved.context,
    quality: { state: "ok" as const, codes: [] },
    export_job_id: "00000000-0000-4000-8000-000000000094",
    work_item_id: "00000000-0000-4000-8000-000000000095",
    status: "completed" as const,
    stage: "completed",
    processed_rows: 9806,
    processed_bytes: 400000,
    total_rows: 9806,
    estimated_bytes: 549136,
    request_fingerprint: "a".repeat(64),
    status_url: "/api/v2/v022/asset-data-exports/job",
    download_url: "/api/v2/v022/asset-data-exports/job/download",
    content_hash: "b".repeat(64),
    byte_size: 400000,
    filename: "assets.zip",
    expires_at: "2026-08-28T00:00:00Z",
    local_delivery_path: "C:\\Users\\tester\\Downloads\\MigratoryBirdExports\\assets.zip",
    error_code: null,
    error_message: null,
  };
  vi.spyOn(api, "createAssetDataExport").mockResolvedValue(completedJob);
  vi.spyOn(api, "assetDataExportStatus").mockResolvedValue(completedJob);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="context" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  const fullExportButton = await screen.findByRole("button", { name: "导出全部已选资产数据" });
  await waitFor(() => expect(fullExportButton).toBeEnabled());
  expect(screen.getByText("已保存 2 项 · 完整日期 · Parquet ZIP")).toBeInTheDocument();
  fireEvent.click(fullExportButton);
  const exactRequest = {
    graphDraftId: saved.graph_draft_id,
    graphDraftRevision: 2,
    exportFormat: "parquet" as const,
    startDate: undefined,
    endDate: undefined,
  };
  await waitFor(() => expect(api.previewAssetDataExport).toHaveBeenCalledWith(exactRequest));
  expect(await screen.findByText("9,806")).toBeInTheDocument();
  expect(screen.getByText(/free_source_retrospective_prices/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "开始后台导出" }));
  await waitFor(() => expect(api.createAssetDataExport).toHaveBeenCalledWith(exactRequest));
  expect(await screen.findByText("导出状态：completed")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载校验后的 ZIP" })).toHaveAttribute(
    "href",
    completedJob.download_url,
  );
});

test("final signal selection is persisted as a draft event and pinned", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view={3} /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "加工层 3" })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "加入聚合输入" }));

  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    {
      expectedRevision: 1,
      eventType: "select_feature_occurrence",
      event: { feature_key: "return_continuation__w120", stage_no: 3 },
    },
  ));
  expect(await screen.findByText("已选择")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /已选择与自动需要/ })).toBeInTheDocument();

  const lineageButton = screen.getByRole("button", { name: "查看血缘" });
  lineageButton.focus();
  fireEvent.click(lineageButton);
  const dialog = screen.getByRole("dialog", { name: "血缘检查器" });
  expect(dialog).toHaveTextContent(
    "return_continuation_node__w120",
  );
  expect(screen.getByRole("button", { name: "关闭血缘" })).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "血缘检查器" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看血缘" })).toHaveFocus();

  fireEvent.change(screen.getByRole("searchbox", { name: "搜索目录" }), {
    target: { value: "不存在的目录" },
  });
  await waitFor(() => expect(api.graphStageFamilies).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({ search: "不存在的目录", stageNo: 3 }),
  ));
  expect(screen.getByRole("heading", { name: /已选择与自动需要/ })).toBeInTheDocument();
});

test("Processing 1 folds raw inputs into a bilingual research definition", async () => {
  await i18n.changeLanguage("en");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view={1} /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Automatic data inputs and pass-through outputs")).toBeInTheDocument();
  expect(screen.getByText("Adjusted close is the frozen price basis for legacy return calculations.")).toBeInTheDocument();
  expect(screen.getByText("vendor total-return adjusted close")).toBeInTheDocument();
  expect(screen.getByText("Published source")).toBeInTheDocument();
  expect(screen.getByText("market_price_scalar")).toBeInTheDocument();
});

test("split processing routes share one Graph Draft provider and exact stage mapping", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/processing-1"]}>
        <Routes>
          <Route element={<GraphRouteHarness />}>
            <Route path="processing-1" element={<GraphWorkspacePage view={1} />} />
            <Route path="processing-2" element={<GraphWorkspacePage view={2} />} />
            <Route path="processing-3" element={<GraphWorkspacePage view={3} />} />
            <Route path="aggregation" element={<GraphWorkspacePage view="aggregation" />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { level: 1, name: "加工层 1" })).toBeInTheDocument();
  await waitFor(() => expect(api.graphStageFamilies).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({ limit: 50, stageNo: 1 }),
  ));
  fireEvent.click(screen.getByRole("link", { name: "Layer 2" }));
  expect(await screen.findByRole("heading", { level: 1, name: "加工层 2" })).toBeInTheDocument();
  await waitFor(() => expect(api.graphStageFamilies).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({ stageNo: 2 }),
  ));
  fireEvent.click(screen.getByRole("link", { name: "Layer 3" }));
  expect(await screen.findByRole("heading", { level: 1, name: "加工层 3" })).toBeInTheDocument();
  await waitFor(() => expect(api.graphStageFamilies).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({ stageNo: 3 }),
  ));
  fireEvent.click(screen.getByRole("link", { name: "Aggregation" }));
  expect(await screen.findByRole("heading", { level: 1, name: "聚合层" })).toBeInTheDocument();
  expect(api.createGraphDraft).toHaveBeenCalledTimes(1);
});

test("strategy page embeds structural resource admission before compile", async () => {
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  await waitFor(() => expect(
    screen.getByRole("button", { name: "重置当前研究" }),
  ).toBeEnabled());
  expect(screen.getByText("v022-m0-policy-v0.22.0")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "配置检查与编译" })).toBeInTheDocument();
  expect(screen.getByText("4 occurrences · 3 edges · 7 work items")).toBeInTheDocument();
  expect(screen.getByText("1 / 7")).toBeInTheDocument();
});

test("retired defense packages stay hidden even in an older persisted projection", async () => {
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "不启用防御" })).toBeInTheDocument();
  expect(screen.queryByText("固定防御预算")).not.toBeInTheDocument();
  expect(screen.queryByText("标普 500 二百日均线分档防御")).not.toBeInTheDocument();
  expect(screen.queryByText("standard_defensive_basket_long_history_v1")).not.toBeInTheDocument();
  const cards = result.container.querySelectorAll(".defense-package-card");
  expect(cards).toHaveLength(1);
  expect(cards[0]).toHaveTextContent("不启用防御");
});

test("strategy families show selected presets first and persist one exact preset-set event", async () => {
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  const selected = await screen.findByRole("checkbox", {
    name: "cross_section_rank_top_k_parity / ETF Top 2",
  });
  const unselected = screen.getByRole("checkbox", {
    name: "cross_section_rank_top_k_parity / ETF Top 1",
  });
  const checkboxes = screen.getAllByRole("checkbox");
  expect(checkboxes.indexOf(selected)).toBeLessThan(checkboxes.indexOf(unselected));

  fireEvent.click(unselected);
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    {
      expectedRevision: 2,
      eventType: "set_strategy_parameter_presets",
      event: {
        strategy_key: "cross_section_rank_top_k_parity",
        preset_keys: ["k1", "k2"],
      },
    },
  ));
});

test("unavailable presets are disabled while unavailable selected presets can be removed", async () => {
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true, true));
  selectedState = true;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  const selectedInvalid = await screen.findByRole("checkbox", {
    name: "cross_section_rank_top_k_large_cap_parity / Large-cap Top 10",
  });
  expect(selectedInvalid).toBeChecked();
  expect(selectedInvalid).toBeEnabled();
  expect(screen.getByRole("checkbox", {
    name: "cross_section_rank_top_k_large_cap_parity / Large-cap Top 20",
  })).toBeDisabled();

  fireEvent.click(selectedInvalid);
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({
      eventType: "set_strategy_parameter_presets",
      event: {
        strategy_key: "cross_section_rank_top_k_large_cap_parity",
        preset_keys: [],
      },
    }),
  ));
});

test("strategy preset mutation disables its whole variant until the revision is confirmed", async () => {
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  let resolveMutation!: (next: GraphDraftSnapshotResponse) => void;
  vi.mocked(api.applyGraphDraftEvent).mockImplementation(() => new Promise((resolve) => {
    resolveMutation = resolve;
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  const k1 = await screen.findByRole("checkbox", {
    name: "cross_section_rank_top_k_parity / ETF Top 1",
  });
  const k2 = screen.getByRole("checkbox", {
    name: "cross_section_rank_top_k_parity / ETF Top 2",
  });
  fireEvent.click(k1);
  await waitFor(() => {
    expect(k1).toBeDisabled();
    expect(k2).toBeDisabled();
  });
  resolveMutation({ ...snapshot(true), revision: 3 });
  await waitFor(() => expect(k1).toBeEnabled());
});

test("configuration review displays the exact strategy preset identity and parameters", async () => {
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText(
    "cross_section_rank_top_k_parity · k2@v1",
  )).toBeInTheDocument();
  expect(screen.getAllByText("目标持仓数=2").length).toBeGreaterThan(0);
});

test("configuration blockers keep whole-graph compilation visibly disabled", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/workspace-v022/strategy"]}>
        <GraphDraftProvider>
          <Routes>
            <Route path="/workspace-v022/strategy" element={<GraphWorkspacePage view="strategy" />} />
            <Route path="/workspace-v022/processing-3" element={<GraphWorkspacePage view={3} />} />
          </Routes>
        </GraphDraftProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("还有配置问题需要处理")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "还没有最终信号" })).toBeInTheDocument();
  expect(screen.getByText("没有最终信号时，聚合器和策略无法产生资产排名。")).toBeInTheDocument();
  expect(screen.getByText("stage3_input_required")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "编译整个研究图" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "前往加工层 3" }));
  expect(await screen.findByRole("heading", { level: 1, name: "加工层 3" })).toBeInTheDocument();
});

test("a successful compile navigates directly to experiment confirmation", async () => {
  await i18n.changeLanguage("en");
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  vi.spyOn(api, "compileGraphDraft").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    compile_attempt_id: "33333333-3333-4333-8333-333333333333",
    compiled_execution_data_context_id: "77777777-7777-4777-8777-777777777777",
    compiled_research_graph_id: "44444444-4444-4444-8444-444444444444",
    draft_intent_id: "55555555-5555-4555-8555-555555555555",
    execution_data_context_artifact_id: "88888888-8888-4888-8888-888888888888",
    execution_data_context_fingerprint: "e".repeat(64),
    execution_data_context_reused: false,
    graph_artifact_id: "66666666-6666-4666-8666-666666666666",
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    graph_draft_revision: 2,
    graph_fingerprint: "f".repeat(64),
    selection_fingerprint: "b".repeat(64),
    reused: false,
  });
  const submitBatch = vi.spyOn(api, "submitGraphSuiteLaunchBatch")
    .mockResolvedValue({
      context: { api_version: "v2", system_version: "0.22.0", read_only: false },
      quality: { state: "ok", codes: [] },
      contract_version: "v0.22.0",
      suite_launch_batch_id: "99999999-9999-4999-8999-999999999999",
      source_graph_draft_id: "11111111-1111-4111-8111-111111111111",
      source_graph_draft_revision: 2,
      batch_fingerprint: "9".repeat(64),
      status: "submitted",
      children: (["weekly", "monthly"] as const).map((frequency) => ({
        frequency,
        graph_draft_id: "11111111-1111-4111-8111-111111111111",
        graph_draft_revision: 2,
        compiled_research_graph_id: "44444444-4444-4444-8444-444444444444",
        research_suite_id: frequency === "weekly"
          ? "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
          : "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status: "not_started" as const,
        total: 0,
        terminal: 0,
        status_counts: {},
        complete: false,
      })),
      reused: false,
    });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/strategy-configuration"]}>
        <GraphDraftProvider>
          <Routes>
            <Route path="/strategy-configuration" element={<GraphWorkspacePage view="strategy" />} />
            <Route path="/experiment-launch" element={<GraphWorkspacePage view="launch" />} />
          </Routes>
        </GraphDraftProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  const compile = await screen.findByRole("button", {
    name: "Compile the whole research graph",
  });
  await waitFor(() => expect(compile).toBeEnabled());
  fireEvent.click(compile);
  expect(await screen.findByRole("heading", {
    name: "Confirm configuration and start",
  })).toBeInTheDocument();
  await waitFor(() => expect(
    screen.getByRole("button", { name: "Start 2 frequency experiments" }),
  ).toBeEnabled());
  expect(screen.getByRole("checkbox", { name: "Weekly" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Monthly" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", {
    name: "Start 2 frequency experiments",
  }));
  await waitFor(() => expect(submitBatch).toHaveBeenCalledWith(expect.objectContaining({
    graphDraftId: "11111111-1111-4111-8111-111111111111",
    graphDraftRevision: 2,
    compiledResearchGraphId: "44444444-4444-4444-8444-444444444444",
    frequencies: ["weekly", "monthly"],
  })));
});

test("experiment launch cannot create a Suite while the runtime is unavailable", async () => {
  await i18n.changeLanguage("en");
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  vi.mocked(api.graphSuiteRuntimeReadiness).mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "warning", codes: ["suite_worker.unavailable"] },
    contract_version: "v0.22.0",
    ready: false,
    state: "unavailable",
    worker_key: null,
    process_id: null,
    heartbeat_at: null,
    max_age_seconds: 10,
  });
  selectedState = true;
  vi.spyOn(api, "currentGraphDraftCompile").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    compile_attempt_id: "33333333-3333-4333-8333-333333333333",
    compiled_execution_data_context_id: "77777777-7777-4777-8777-777777777777",
    compiled_research_graph_id: "44444444-4444-4444-8444-444444444444",
    draft_intent_id: "55555555-5555-4555-8555-555555555555",
    execution_data_context_artifact_id: "88888888-8888-4888-8888-888888888888",
    execution_data_context_fingerprint: "e".repeat(64),
    execution_data_context_reused: false,
    graph_artifact_id: "66666666-6666-4666-8666-666666666666",
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    graph_draft_revision: 2,
    graph_fingerprint: "f".repeat(64),
    selection_fingerprint: "b".repeat(64),
    reused: true,
  });
  const submit = vi.spyOn(api, "submitGraphSuiteLaunchBatch");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/experiment-launch"]}>
        <GraphDraftProvider><GraphWorkspacePage view="launch" /></GraphDraftProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Backtest runtime is not ready")).toBeInTheDocument();
  const start = screen.getByRole("button", {
    name: "Start 2 frequency experiments",
  });
  expect(start).toBeDisabled();
  fireEvent.click(start);
  expect(submit).not.toHaveBeenCalled();
});

test("a partial execution-context identity never marks a compile as current", async () => {
  await i18n.changeLanguage("en");
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  vi.spyOn(api, "compileGraphDraft").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    compile_attempt_id: "33333333-3333-4333-8333-333333333333",
    compiled_execution_data_context_id: "77777777-7777-4777-8777-777777777777",
    compiled_research_graph_id: "44444444-4444-4444-8444-444444444444",
    draft_intent_id: "55555555-5555-4555-8555-555555555555",
    graph_artifact_id: "66666666-6666-4666-8666-666666666666",
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    graph_draft_revision: 2,
    graph_fingerprint: "f".repeat(64),
    selection_fingerprint: "b".repeat(64),
    reused: false,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  const compile = await screen.findByRole("button", {
    name: "Compile the whole research graph",
  });
  await waitFor(() => expect(compile).toBeEnabled());
  fireEvent.click(compile);
  await waitFor(() => expect(api.compileGraphDraft).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("f".repeat(64))).not.toBeInTheDocument();
});

test("a stale selected defense must be cleared before compiling", async () => {
  await i18n.changeLanguage("en");
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true, false, "fixed20_defense"));
  selectedState = true;
  vi.spyOn(api, "compileGraphDraft").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    compile_attempt_id: "33333333-3333-4333-8333-333333333333",
    compiled_execution_data_context_id: "77777777-7777-4777-8777-777777777777",
    compiled_research_graph_id: "44444444-4444-4444-8444-444444444444",
    draft_intent_id: "55555555-5555-4555-8555-555555555555",
    execution_data_context_artifact_id: "88888888-8888-4888-8888-888888888888",
    execution_data_context_fingerprint: "e".repeat(64),
    execution_data_context_reused: false,
    graph_artifact_id: "66666666-6666-4666-8666-666666666666",
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    graph_draft_revision: 2,
    graph_fingerprint: "f".repeat(64),
    selection_fingerprint: "b".repeat(64),
    reused: false,
    defense_execution_contexts: [],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  const compile = await screen.findByRole("button", {
    name: "Compile the whole research graph",
  });
  expect(compile).toBeDisabled();
  expect(screen.getByText("The prior defense package is retired")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Use no defense" }));
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({ eventType: "clear_defenses", event: {} }),
  ));
  await waitFor(() => expect(compile).toBeEnabled());
  fireEvent.click(compile);
  await waitFor(() => expect(api.compileGraphDraft).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("fixed20_defense")).not.toBeInTheDocument();
});

test("a paused mutation queue keeps whole-graph compilation disabled", async () => {
  vi.mocked(api.createGraphDraft).mockResolvedValue(snapshot(true));
  selectedState = true;
  vi.mocked(api.applyGraphDraftEvent).mockRejectedValueOnce(new ApiClientError("stale revision", 409, "draft_revision_conflict"));
  vi.spyOn(api, "graphDraft").mockResolvedValue({ ...snapshot(true), revision: 3 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  const compile = await screen.findByRole("button", { name: "编译整个研究图" });
  await waitFor(() => expect(compile).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "使用不防御" }));
  await waitFor(() => expect(compile).toBeDisabled());
  expect(await screen.findByText("stale revision")).toBeInTheDocument();
});

test("rapid commands use each server-confirmed revision in order", async () => {
  vi.mocked(api.applyGraphDraftEvent).mockImplementation(async (_draftId, input) => ({
    ...snapshot(true),
    revision: input.expectedRevision + 1,
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <GraphDraftProvider><QueueHarness /></GraphDraftProvider>
    </QueryClientProvider>,
  );
  const first = await screen.findByRole("button", { name: "Queue first" });
  await waitFor(() => expect(first).toBeEnabled());
  fireEvent.click(first);
  fireEvent.click(screen.getByRole("button", { name: "Queue second" }));

  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.applyGraphDraftEvent).mock.calls[0][1].expectedRevision).toBe(1);
  expect(vi.mocked(api.applyGraphDraftEvent).mock.calls[1][1].expectedRevision).toBe(2);
  await waitFor(() => expect(screen.getByLabelText("pending commands")).toHaveTextContent("0"));
});

test("revision conflict pauses remaining commands until reload", async () => {
  vi.mocked(api.applyGraphDraftEvent)
    .mockRejectedValueOnce(new ApiClientError("stale revision", 409, "draft_revision_conflict"))
    .mockImplementationOnce(async (_draftId, input) => ({
      ...snapshot(true),
      revision: input.expectedRevision + 1,
    }));
  vi.spyOn(api, "graphDraft").mockResolvedValue({ ...snapshot(true), revision: 5 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <GraphDraftProvider><QueueHarness /></GraphDraftProvider>
    </QueryClientProvider>,
  );
  const first = await screen.findByRole("button", { name: "Queue first" });
  await waitFor(() => expect(first).toBeEnabled());
  fireEvent.click(first);
  fireEvent.click(screen.getByRole("button", { name: "Queue second" }));

  await waitFor(() => expect(screen.getByLabelText("queue state")).toHaveTextContent("paused"));
  expect(api.applyGraphDraftEvent).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Resume" }));
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.applyGraphDraftEvent).mock.calls[1][1].expectedRevision).toBe(5);
  expect(screen.getByLabelText("queue state")).toHaveTextContent("running");
});

test("a confirmed revision is broadcast and reloaded by another tab", async () => {
  vi.stubGlobal("BroadcastChannel", TestBroadcastChannel);
  vi.mocked(api.applyGraphDraftEvent).mockResolvedValue({ ...snapshot(true), revision: 2 });
  vi.spyOn(api, "graphDraft").mockResolvedValue({ ...snapshot(true), revision: 2 });
  const firstClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const secondClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<>
    <QueryClientProvider client={firstClient}>
      <GraphDraftProvider><QueueHarness prefix="First " /></GraphDraftProvider>
    </QueryClientProvider>
    <QueryClientProvider client={secondClient}>
      <GraphDraftProvider><QueueHarness prefix="Second " /></GraphDraftProvider>
    </QueryClientProvider>
  </>);
  const first = await screen.findByRole("button", { name: "First Queue first" });
  const second = await screen.findByRole("button", { name: "Second Queue first" });
  await waitFor(() => {
    expect(first).toBeEnabled();
    expect(second).toBeEnabled();
  });

  fireEvent.click(first);

  await waitFor(() => expect(screen.getByLabelText("Second revision")).toHaveTextContent("2"));
  expect(api.graphDraft).toHaveBeenCalled();
});

test("aggregation presets and the no-defense reset are persisted as explicit events", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="aggregation" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(
    screen.getByRole("button", { name: "重置当前研究" }),
  ).toBeEnabled());

  fireEvent.click(screen.getByRole("checkbox", { name: "signal_equal_v1" }));
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({
      expectedRevision: 1,
      eventType: "set_aggregation_parameter_presets",
      event: { family_key: "flat_equal_weight_mean", preset_keys: [] },
    }),
  ));

  result.rerender(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="strategy" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "策略与防御" });
  fireEvent.click(screen.getByRole("button", { name: "使用不防御" }));
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({
      expectedRevision: 2,
      eventType: "clear_defenses",
      event: {},
    }),
  ));
});

test("legacy hierarchical recipes are disabled before compile when inputs do not match", async () => {
  const unavailable = snapshot(true);
  const baseAggregation = unavailable.derived_view.aggregations[0];
  unavailable.derived_view.aggregations = [{
    ...baseAggregation,
    family_key: "hierarchical_weighted_mean",
    name: "Hierarchical weighted mean",
    selected: true,
    parameter_presets: ["legacy_dimension_equal_v1"],
    selected_parameter_presets: [],
    parameter_preset_definitions: [{
      preset_key: "legacy_dimension_equal_v1",
      name: "Legacy dimension equal weight",
      description: "Only exact migrated signal sets are supported.",
      version_number: 1,
      semantics: { weight_policy: "dimension_equal" },
      selected: false,
      selectable: false,
      reason_codes: ["aggregation_recipe_unavailable"],
    }],
  }];
  unavailable.derived_view.blockers = [{
    layer: "aggregation",
    object_key: "hierarchical_weighted_mean:legacy_dimension_equal_v1",
    reason_codes: ["aggregation_recipe_unavailable"],
    feature_keys: ["return_continuation__w120"],
  }];
  vi.mocked(api.createGraphDraft).mockResolvedValue(unavailable);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="aggregation" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", { name: "分层加权平均" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "legacy_dimension_equal_v1" })).toBeDisabled();
  expect(screen.getByText("当前信号组合没有对应的已发布聚合方案")).toBeInTheDocument();
  expect(screen.getByText("选择聚合方案（必选）")).toBeInTheDocument();
});

test("native hierarchical recipe is localized and selectable when taxonomy accepts inputs", async () => {
  const available = snapshot(true);
  const baseAggregation = available.derived_view.aggregations[0];
  available.derived_view.aggregations = [{
    ...baseAggregation,
    family_key: "hierarchical_weighted_mean",
    name: "Hierarchical weighted mean",
    selected: true,
    parameter_presets: ["active_dimension_equal_component_equal_v1"],
    selected_parameter_presets: [],
    parameter_preset_definitions: [{
      preset_key: "active_dimension_equal_component_equal_v1",
      name: "Native equal dimensions and components",
      description: "Equal active dimensions and equal selected signals inside each dimension.",
      version_number: 1,
      semantics: {
        dimension_weight_policy: "equal_active_dimensions",
        component_weight_policy: "equal_selected_components",
      },
      selected: false,
      selectable: true,
      reason_codes: [],
    }],
  }];
  vi.mocked(api.createGraphDraft).mockResolvedValue(available);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="aggregation" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("原生维度与维度内信号双层等权")).toBeInTheDocument();
  const preset = screen.getByRole("checkbox", {
    name: "active_dimension_equal_component_equal_v1",
  });
  expect(preset).toBeEnabled();
  fireEvent.click(preset);
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({
      eventType: "set_aggregation_parameter_presets",
      event: {
        family_key: "hierarchical_weighted_mean",
        preset_keys: ["active_dimension_equal_component_equal_v1"],
      },
    }),
  ));
});

test("supervised aggregation persists explicit target and training member selections", async () => {
  const supervised = snapshot(true);
  const baseAggregation = supervised.derived_view.aggregations[0];
  supervised.derived_view.aggregations = [{
    ...baseAggregation,
    family_key: "ols_cross_sectional_regression",
    name: "OLS cross-sectional regression",
    execution_mode: "supervised",
    selected: true,
    parameter_presets: [],
    parameter_preset_definitions: [],
    selected_parameter_presets: [],
    targets: [{
      key: "forward_rank_h5",
      name: "Forward H5 rank",
      description: "Five-session forward cross-sectional rank.",
      version_number: 1,
      semantics: { horizon_sessions: 5 },
      selected: false,
    }],
    selected_targets: [],
    training_presets: [{
      key: "expanding_daily_ols_v1",
      name: "Expanding daily OLS",
      description: "Strict expanding walk-forward OLS.",
      version_number: 1,
      semantics: { random_split: false },
      selected: false,
    }],
    selected_training_presets: [],
  }];
  vi.mocked(api.createGraphDraft).mockResolvedValue(supervised);
  vi.mocked(api.applyGraphDraftEvent).mockResolvedValue(supervised);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter><GraphDraftProvider><GraphWorkspacePage view="aggregation" /></GraphDraftProvider></MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("OLS 横截面线性回归")).toBeInTheDocument();
  expect(screen.getByText("未来 5 个交易日横截面排名")).toBeInTheDocument();
  expect(screen.getByText("扩展窗口日频 OLS")).toBeInTheDocument();
  expect(screen.getByText(/恰好 5 个完整交易日/)).toBeInTheDocument();
  expect(screen.getByText(/确定性的扩展窗口 OLS/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("checkbox", { name: "forward_rank_h5" }));
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({
      eventType: "set_aggregation_targets",
      event: {
        family_key: "ols_cross_sectional_regression",
        target_keys: ["forward_rank_h5"],
      },
    }),
  ));

  fireEvent.click(screen.getByRole("checkbox", { name: "expanding_daily_ols_v1" }));
  await waitFor(() => expect(api.applyGraphDraftEvent).toHaveBeenCalledWith(
    "11111111-1111-4111-8111-111111111111",
    expect.objectContaining({
      eventType: "set_aggregation_training_presets",
      event: {
        family_key: "ols_cross_sectional_regression",
        preset_keys: ["expanding_daily_ols_v1"],
      },
    }),
  ));
});
