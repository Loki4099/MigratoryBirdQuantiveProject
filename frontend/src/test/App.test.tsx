import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { AppRoutes } from "../App";
import { api } from "../api/client";
import i18n from "../i18n";

const health = {
  context: { api_version: "v2", system_version: "0.22.0", read_only: true },
  quality: { state: "ok", codes: [] },
  database_revision: "20260802_02_v02_lineage",
};
const capabilities = {
  context: health.context, quality: health.quality,
  domains: [{ key: "lineage", purpose: "Lineage", upstream: [], delivery_milestone: "M1C", availability: "available" }],
  endpoints: ["health", "artifacts"], interface_states: ["loading", "tainted"], languages: ["zh-CN", "en"],
};
const artifacts = {
  context: health.context, quality: health.quality, items: [], total: 0, limit: 100, offset: 0,
};
const hiddenReleaseControl = {
  context: health.context,
  quality: health.quality,
  state: "hidden",
  transition_sequence: 0,
  transition_artifact_id: null,
  default_contract: "v0.21",
  maintenance_read_only: false,
  shadow_runtime_allowed: false,
  v021_research_creation_allowed: true,
  v022_explicit_creation_allowed: false,
};
const graphDraftSnapshot = {
  context: { api_version: "v2", system_version: "0.22.0", read_only: false },
  quality: { state: "warning", codes: [] },
  graph_draft_id: "11111111-1111-4111-8111-111111111111",
  catalog_release_id: "22222222-2222-4222-8222-222222222222",
  draft_key: "browser_default_v1",
  name: "v0.22 Graph Workspace",
  status: "draft",
  revision: 1,
  intent: { frequency: "weekly", aggregation_family_keys: [], aggregation_parameter_preset_keys: {}, strategy_keys: [], strategy_parameter_preset_keys: {}, defense_keys: [], explicit_features: [] },
  asset_context: { asset_context_key: "us_style_rotation_4_etf_sample_v1" },
  resolved_data_binding: { data_input_keys: ["canonical_market_bars"] },
  applied: true,
  derived_view: {
    catalog_release: { release_key: "bird_v022_catalog", catalog_version: "0.22.1", contract_version: "v0.22.0", source_manifest_hash: "a".repeat(64) },
    selection_fingerprint: "b".repeat(64),
    derived_state_fingerprint: "c".repeat(64),
    frequency: "weekly",
    summary: { explicit_count: 0, required_count: 0, stage3_input_count: 0, aggregation_instance_count: 0, strategy_branch_count: 0, backtest_cell_count: 0 },
    aggregation_inputs: [], aggregations: [], strategies: [], defenses: [],
    stages: [0, 1, 2, 3].map((stage_no) => ({
      stage_no, explicit_count: 0, required_count: 0, families: [],
    })),
    blockers: [{ layer: "stage3", object_key: "aggregation_inputs", reason_codes: ["stage3_input_required"], feature_keys: [] }],
    warnings: [],
    resources: { policy_id: "v022-m0-policy-v0.22.0", state: "accepted", estimates: { explicit_stage3_inputs: 0, feature_occurrences: 0, ancestor_occurrences: 0, graph_edges: 0, aggregation_candidates: 0, aggregation_instances: 0, strategy_candidates: 0, defense_candidates: 0, strategy_branches: 0, backtest_cells: 0, work_items: 0 }, checks: [], reason_codes: [] },
  },
};
const assetCatalog = {
  context: health.context,
  quality: health.quality,
  release_artifact_id: "00000000-0000-0000-0000-000000000021",
  release_version_number: 21001,
  catalog_version: "0.21.0",
  as_of_date: "2026-08-05",
  total: 1,
  limit: 200,
  offset: 0,
  categories: [{ category_key: "stocks", name: "Stocks", description: "Stable identities", asset_count: 1 }],
  asset_sets: [{ set_key: "sample", name: "Sample", set_type: "fixed", maturity: "strategy_ready", formal_eligible: true, notes: "Development fixture", member_security_ids: ["00000000-0000-0000-0000-000000000022"] }],
  items: [{
    security_id: "00000000-0000-0000-0000-000000000022",
    asset_id: "00000000-0000-0000-0000-000000000023",
    asset_key: "aapl", name: "Apple Inc.", category_key: "stocks", asset_class: "Equity",
    instrument_type: "Common Stock", status: "active", symbol: "AAPL", aliases: ["Apple", "APPL"],
    venue_mic: "XNAS", currency: "USD", calendar_key: "XNYS", tradability: "tradable",
    tags: ["technology"], maturity: "research_ready", target_maturity: "strategy_ready",
    missing_requirements: ["pit_master_data"], canonical_data_available: true, selectable: true,
    data_inputs: [
      { input_key: "canonical_market_bars", name: "Canonical OHLCV and adjusted prices", source_kind: "market", available: true, selectable: true, point_in_time: true, downstream_factor_keys: ["close_raw", "close_adj"], status_note: "published canonical history" },
      { input_key: "sec_filing_fundamentals", name: "Filed fundamental facts", source_kind: "fundamental", available: false, selectable: false, point_in_time: true, downstream_factor_keys: ["pe_ratio", "roe"], status_note: "planned" },
    ],
  }],
};
const assetSeries = {
  context: health.context,
  quality: health.quality,
  security_id: "00000000-0000-0000-0000-000000000022",
  asset_key: "aapl", symbol: "AAPL",
  dataset_artifact_id: "00000000-0000-0000-0000-000000000024",
  dataset_version_number: 1, coverage_start: "2026-08-03", coverage_end: "2026-08-04",
  points: [
    { session_date: "2026-08-03", open: 200, high: 205, low: 199, close: 204, adjusted_close: 204, volume: 1000 },
    { session_date: "2026-08-04", open: 204, high: 206, low: 202, close: 205, adjusted_close: 205, volume: 900 },
  ],
};
const workspaceOptions = {
  context: health.context, quality: health.quality,
  catalog_artifact_id: "00000000-0000-0000-0000-000000000025", catalog_version: "0.21.0",
  frequency: "weekly",
  model_target_options: [
    { target_key: "future_return__h5", target_kind: "future_return", horizon_sessions: 5, recommended: true },
    { target_key: "cross_sectional_relative_return__h5", target_kind: "cross_sectional_relative_return", horizon_sessions: 5, recommended: true },
  ],
  unknown_factor_variant_keys: [], unknown_signal_version_keys: [],
  unknown_model_preset_keys: [], selected_asset_count: 4, usable_asset_count: 4,
  asset_data_input_blockers: [],
  selected_asset_type_counts: { "Equity ETF": 4 },
  factor_families: [{ key: "total_return", family: "return", definition_version: 1,
    formula: "close_adj[t] / close_adj[t-window] - 1", inputs: ["close_adj"], required_asset_input_keys: ["canonical_market_bars"],
    implementation_key: "total_return_v1", output_unit: "dimensionless",
    time_semantics: "known_at_session_close", raw: false,
    variants: [{ key: "total_return__w120", parameters: { window: 120 },
      required_price_observations: 121, preset_type: "canonical", selected: false,
      selectable: true, reason_codes: [] }] }],
  signal_families: [{ key: "return_continuation", factor_variants: ["total_return__w120"],
    form: "continuous", output_type: "continuous", direction: "higher_is_better", rule: null,
    economic_family: "momentum", dimension_hint: "momentum_trend", rationale_type: "academic",
    rationale: "Relative return strength may persist.", research_tier: "canonical", product_eligible: true,
    versions: [{ version_key: "return_continuation__total_return__w120",
      factor_variant_key: "total_return__w120", selected: false, selectable: false,
      reason_codes: ["factor_not_selected"] }] }],
  model_families: [{ key: "linear_weighted", name: "Deterministic linear aggregation",
    description: "All selected continuous Signals enter one model.", implementation_status: "available",
    presets: [{ preset_key: "linear_weighted__signal_equal_v1", output_type: "continuous_score",
      output_comparability: "cross_sectional", supported_frequencies: ["weekly", "monthly"],
      parameters: { weighting: "equal_by_signal" }, target_key: null,
      input_slots: [{ slot_key: "continuous_inputs", allowed_dimension_keys: ["momentum_trend"],
        allowed_output_types: ["continuous"], minimum_count: 1, maximum_count: 16 }],
      selectable: false, reason_codes: ["slot_underflow"], accepted_signal_keys: [] }] }],
  strategy_families: [{ key: "multi_etf_top_k", name: "Multi-ETF Cross-sectional Top-K Rotation",
    description: "Ranks selected ETFs by one comparable Model score.", implementation_status: "available",
    required_instrument_type: "Equity ETF", minimum_eligible_assets: 2,
    formal_minimum_eligible_assets: 2, coverage_ratio: 0.9,
    supported_frequencies: ["weekly", "monthly"], compatible_model_output_types: ["continuous_score"],
    parameter_options: { target_k: [1, 2], defense: ["none"], selection_buffer: ["none"], sector_cap: ["none"] },
    defaults: { target_k: 2, defense: "none", selection_buffer: "none", sector_cap: "none" },
    primary_benchmark: "spy_buy_and_hold", research_benchmark: "selected_etf_equal_weight_same_schedule",
    presets: [{ preset_key: "multi_etf_top_k__k2__none__none__none",
      parameters: { target_k: 2, defense: "none", selection_buffer: "none", sector_cap: "none" },
      selected: false, selectable: false, reason_codes: ["model_not_selected"], research_mode: "formal" }] }],
};
const dataOverview = {
  context: health.context,
  quality: { state: "warning", codes: ["data.ineligible_assets"] },
  sources: [],
  datasets: [{
    artifact_id: "00000000-0000-0000-0000-000000000001",
    dataset_key: "us_style_daily_bars",
    version_number: 1,
    dataset_kind: "canonical",
    value_kind: "daily_bar",
    coverage_start: "2020-01-02",
    coverage_end: "2026-07-31",
    row_count: 6500,
    coverage: [{ subject_key: "IWD", asset_key: "iwd", coverage_start: "2020-01-02", coverage_end: "2026-07-31", observation_count: 1600, missing_count: 0 }],
    issues: [], quality: { state: "ok", codes: [] },
  }],
  bundle: null,
  eligibility: null,
};
const factorOverview = {
  context: health.context,
  quality: health.quality,
  diagnostic_artifact_id: "00000000-0000-0000-0000-000000000010",
  factor_catalog_artifact_id: "00000000-0000-0000-0000-000000000011",
  universe_artifact_id: "00000000-0000-0000-0000-000000000012",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000013",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000014",
  factor_engine_artifact_id: "00000000-0000-0000-0000-000000000015",
  diagnostic_engine_artifact_id: "00000000-0000-0000-0000-000000000016",
  coverage_start: "2025-01-02", coverage_end: "2026-01-30",
  dataset_count: 1, asset_count: 5, observation_count: 100, pair_count: 1,
  high_correlation_threshold: 0.85,
  datasets: [{
    factor_dataset_artifact_id: "00000000-0000-0000-0000-000000000017",
    factor_key: "total_return", measurement_family: "return",
    formula: "close[t] / close[t-window] - 1", output_unit: "ratio",
    variant_key: "total_return__w20", parameters: { window: 20 }, preset_type: "canonical",
    coverage_start: "2025-01-02", coverage_end: "2026-01-30", row_count: 100,
    observation_count: 100, asset_count: 5, missing_count: 0,
    mean: 0.02, standard_deviation: 0.05, minimum: -0.1, p05: -0.05,
    p25: -0.01, median: 0.015, p75: 0.04, p95: 0.09, maximum: 0.12,
    zero_variance: false, quality: health.quality,
  }],
  correlations: [{
    left_variant_key: "total_return__w20", right_variant_key: "total_return__w60",
    left_factor_key: "total_return", right_factor_key: "total_return", observation_count: 100,
    spearman_correlation: 0.91, same_definition: true, high_correlation: true,
  }],
  issues: [],
};
const signalOverview = {
  context: health.context,
  quality: { state: "warning", codes: ["signal.diagnostic_warning"] },
  evaluation_artifact_id: "00000000-0000-0000-0000-000000000020",
  signal_catalog_artifact_id: "00000000-0000-0000-0000-000000000021",
  universe_artifact_id: "00000000-0000-0000-0000-000000000022",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000023",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000024",
  signal_engine_artifact_id: "00000000-0000-0000-0000-000000000025",
  evaluation_engine_artifact_id: "00000000-0000-0000-0000-000000000026",
  forward_return_artifact_id: "00000000-0000-0000-0000-000000000027",
  target_key: "weekly_next_open_to_next_open", frequency: "weekly",
  coverage_start: "2025-01-03", coverage_end: "2026-01-30",
  signal_count: 1, common_period_count: 52, pair_count: 1,
  high_correlation_threshold: 0.85,
  signals: [{
    signal_dataset_artifact_id: "00000000-0000-0000-0000-000000000028",
    signal_key: "return_continuation__total_return__w252",
    template_key: "return_continuation", economic_family: "momentum",
    rationale_type: "academic", rationale: "Persistent relative performance may continue.",
    research_tier: "canonical", product_eligible: true, direction: "higher_is_better",
    normalization: "cross_sectional_centered_rank_-1_1", output_type: "continuous",
    factor_variant_key: "total_return__w252", quality: health.quality,
    full: {
      window_key: "full", window_start: "2025-01-03", window_end: "2026-01-30",
      period_count: 52, valid_ic_count: 51, undefined_ic_count: 1,
      mean_rank_ic: 0.18, median_rank_ic: 0.2, positive_ic_ratio: 0.62,
      information_ratio: 1.1, mean_top_bottom_spread: 0.003, event_rate: null,
      event_asset_concentration: null, non_neutral_rate: 1, mean_top2_turnover: 0.22,
    },
    stability: [{
      window_key: "year:2025", window_start: "2025-01-03", window_end: "2025-12-26",
      period_count: 51, valid_ic_count: 50, undefined_ic_count: 1,
      mean_rank_ic: 0.17, median_rank_ic: 0.2, positive_ic_ratio: 0.6,
      information_ratio: 1, mean_top_bottom_spread: 0.0028, event_rate: null,
      event_asset_concentration: null, non_neutral_rate: 1, mean_top2_turnover: 0.2,
    }],
  }],
  pairs: [{
    left_signal_key: "return_continuation__total_return__w252",
    right_signal_key: "return_continuation__total_return__w120",
    score_observation_count: 208, score_spearman: 0.9, spread_period_count: 52,
    spread_correlation: 0.75, mean_top2_overlap: 0.8, high_correlation: true,
  }],
  issues: [{
    signal_key: "return_continuation__total_return__w252", severity: "warning",
    issue_code: "short_evaluation_sample", message: "Short sample", details: {},
  }],
};
const modelOverview = {
  context: health.context,
  quality: { state: "warning", codes: ["model.diagnostic_warning"] },
  evaluation_artifact_id: "00000000-0000-0000-0000-000000000030",
  model_catalog_artifact_id: "00000000-0000-0000-0000-000000000031",
  universe_artifact_id: "00000000-0000-0000-0000-000000000032",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000033",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000034",
  model_engine_artifact_id: "00000000-0000-0000-0000-000000000035",
  evaluation_engine_artifact_id: "00000000-0000-0000-0000-000000000036",
  forward_return_artifact_id: "00000000-0000-0000-0000-000000000037",
  target_key: "weekly_next_open_to_next_open", frequency: "weekly",
  coverage_start: "2025-01-03", coverage_end: "2026-01-30",
  model_count: 1, common_period_count: 52, pair_count: 1, ablation_count: 1,
  high_correlation_threshold: 0.85,
  models: [{
    model_dataset_artifact_id: "00000000-0000-0000-0000-000000000038",
    specification_key: "dimension_equal_weight__momentum_trend+volatility_risk",
    specification_type: "dimension_subset_equal_weight",
    model_key: "classic_market_composite", model_family: "cross_sectional_composite",
    hypothesis: "Complementary dimensions may improve robustness.",
    overall_method_key: "weighted_mean", tie_output: "not_applicable",
    output_type: "continuous_score", active_dimension_count: 2, component_count: 1,
    research_tier: "canonical", quality: { state: "warning", codes: ["model.diagnostic_warning"] },
    dimensions: [{ dimension_key: "momentum_trend", method_key: "weighted_mean", input_transform: "identity", weight: 0.5,
      components: [{ signal_key: "return_continuation__total_return__w252", input_transform: "identity", weight: 1 }] }],
    full: { window_key: "full", window_start: "2025-01-03", window_end: "2026-01-30", period_count: 52,
      valid_ic_count: 51, undefined_ic_count: 1, mean_rank_ic: 0.21, median_rank_ic: 0.2,
      positive_ic_ratio: 0.64, information_ratio: 1.2, mean_top_bottom_spread: 0.0035,
      non_neutral_rate: 1, mean_top2_turnover: 0.2, mean_score_dispersion: 0.41, mean_confidence: 0.55 },
    stability: [],
  }],
  pairs: [{ left_specification_key: "model_a", right_specification_key: "model_b",
    score_observation_count: 208, score_spearman: 0.91, spread_period_count: 52,
    spread_correlation: 0.7, mean_top2_overlap: 0.8, high_correlation: true }],
  ablations: [{ full_specification_key: "dimension_equal_weight__momentum_trend+volatility_risk",
    ablated_specification_key: "dimension_equal_weight__momentum_trend",
    removed_dimension_key: "volatility_risk", window_key: "full", period_count: 52,
    delta_mean_rank_ic: 0.03, delta_information_ratio: 0.1, delta_mean_top_bottom_spread: 0.0004 }],
  issues: [{ specification_key: "dimension_equal_weight__momentum_trend+volatility_risk",
    severity: "warning", issue_code: "short_evaluation_sample", message: "Short sample", details: {} }],
};
const strategyOverview = {
  context: health.context, quality: health.quality,
  rules: {
    definition_artifact_id: "00000000-0000-0000-0000-000000000040",
    version_artifact_id: "00000000-0000-0000-0000-000000000041",
    strategy_key: "us_style_cross_sectional_rotation",
    strategy_family: "cross_sectional_top_k_rotation",
    hypothesis: "Higher-scored candidates may outperform.", version_number: 1,
    selection_contract: "rank_model_scores", allocation_contract: "equal_slot_budget",
    reserve_contract: "unused_slot_budget_to_synthetic_reserve",
    compatible_model_output_types: ["continuous_score", "directional_score"],
    candidate_input_policy: "complete_eligible_universe", missing_input_policy: "fail_formal_run",
    variants: [{ artifact_id: "00000000-0000-0000-0000-000000000042",
      variant_key: "top_k_equal_weight__k2", template_key: "top_k_equal_weight",
      target_k: 2, research_tier: "canonical", selection_order: "rank_then_select",
      trend_filter: "none", auxiliary_signal_key: null, auxiliary_eligible_state: null,
      empty_slot_policy: "not_applicable", tie_policy: "proportional_share_of_remaining_slot_budget",
      slot_weight_rule: "1 / K", reserve_rule: "unused_slot_budget_to_synthetic_reserve" }],
    schedules: [{ artifact_id: "00000000-0000-0000-0000-000000000043",
      schedule_key: "weekly_last_common_session_close", frequency: "weekly",
      decision_timing: "last_common_session_close", decision_data_policy: "include_decision_close" }],
    execution_policy: { artifact_id: "00000000-0000-0000-0000-000000000044",
      policy_key: "next_common_session_open", delay_common_sessions: 1,
      execution_price: "adjusted_open", missing_execution_policy: "fail_formal_run" },
  },
  products: [{ artifact_id: "00000000-0000-0000-0000-000000000045", product_key: "product-a",
    version_number: 1, model_specification_key: "dimension_equal_weight__momentum_trend",
    model_specification_type: "dimension_subset_equal_weight", model_output_type: "continuous_score",
    variant_key: "top_k_equal_weight__k2", target_k: 2, research_tier: "canonical",
    universe_key: "us_style_rotation_core", schedule_key: "weekly_last_common_session_close",
    frequency: "weekly", execution_policy_key: "next_common_session_open",
    execution_price: "adjusted_open", target_path_count: 1 }],
  target_paths: [{ artifact_id: "00000000-0000-0000-0000-000000000046",
    product_artifact_id: "00000000-0000-0000-0000-000000000045", product_key: "product-a",
    model_dataset_artifact_id: "00000000-0000-0000-0000-000000000047",
    model_specification_key: "dimension_equal_weight__momentum_trend",
    variant_key: "top_k_equal_weight__k2", target_k: 2, frequency: "weekly",
    coverage_start: "2026-01-02", coverage_end: "2026-01-09", decision_count: 2,
    position_count: 8 }],
};
const strategyTarget = {
  context: health.context, quality: health.quality, target_path: strategyOverview.target_paths[0],
  universe_artifact_id: "00000000-0000-0000-0000-000000000048",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000049",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000050",
  engine_artifact_id: "00000000-0000-0000-0000-000000000051",
  auxiliary_signal_dataset_artifact_id: null,
  decisions: [{ decision_date: "2026-01-09", target_k: 2, actual_holding_count: 2,
    boundary_tie_count: 0, reserve_target_weight: 0,
    positions: [{ asset_key: "iwd", symbol: "IWD", model_score: 0.8, model_rank: 1,
      selection_rank: 1, trend_state: null, strategy_eligible: true, selected: true,
      target_weight: 0.5, decision_reason: "selected_by_rank" }] }],
};
const experimentOverview = {
  context: health.context, quality: health.quality,
  total_specification_count: 1, filtered_specification_count: 1,
  accepted_count: 1, failed_count: 0, running_count: 0, pending_count: 0,
  limit: 50, offset: 0,
  suites: [{ artifact_id: "00000000-0000-0000-0000-000000000060", suite_key: "formal-v02",
    version_number: 1, name: "Formal v0.2", description: "Comparable cells", specification_count: 1 }],
  specifications: [{ artifact_id: "00000000-0000-0000-0000-000000000061",
    result_artifact_id: "00000000-0000-0000-0000-000000000062",
    suite_artifact_id: "00000000-0000-0000-0000-000000000060", cell_key: "weekly-full-5bps",
    ordinal: 0, product_key: "product-a", model_specification_key: "dimension_equal_weight__momentum_trend",
    variant_key: "top_k_equal_weight__k2", frequency: "weekly", benchmark_key: "spy_buy_and_hold",
    benchmark_category: "product_primary", cost_bps_per_side: 5, template_key: "full_history",
    initialization_policy: "carry_in", as_of_date: "2026-01-09", simulation_end: "2026-01-09",
    status: "accepted", availability_status: "eligible", quality_status: "normal", attempt_number: 1,
    error_summary: null, core_metrics: { "strategy.cagr": 0.12, "benchmark.cagr": 0.09,
      "strategy.sharpe_ratio": 1.1, "strategy.maximum_drawdown": -0.08 } }],
};
const experimentResult = {
  context: health.context, quality: health.quality, result_artifact_id: "00000000-0000-0000-0000-000000000062",
  specification: experimentOverview.specifications[0], interval_result_artifact_id: "00000000-0000-0000-0000-000000000063",
  requested_start: "2025-01-02", requested_end: "2026-01-09", resolved_start: "2025-01-02",
  resolved_end: "2026-01-09", normalization_nav_date: "2025-01-02", observation_count: 252,
  metric_value_count: 36, run_attempt_id: "00000000-0000-0000-0000-000000000064", run_status: "completed",
  started_at: "2026-01-10T00:00:00Z", completed_at: "2026-01-10T00:01:00Z",
  metrics: [{ series_role: "strategy", metric_scope: "absolute", metric_key: "cagr", name: "CAGR",
    unit: "annual_ratio", value: 0.12, value_status: "defined", reason_code: null, observation_count: 252 }],
  events: [{ sequence_number: 1, event_type: "run_started", severity: "info", message: "Started",
    occurred_at: "2026-01-10T00:00:00Z" }],
  quality_checks: [{ check_key: "outputs_published", scope_key: "global", status: "passed",
    severity: "info", message: "All outputs published" }], artifacts: [],
  nav_series: [
    { nav_date: "2025-01-02", strategy_wealth: 1, benchmark_wealth: 1, excess_wealth: 1, drawdown: 0 },
    { nav_date: "2026-01-09", strategy_wealth: 1.12, benchmark_wealth: 1.09, excess_wealth: 1.0275, drawdown: 0 },
  ],
  promotion_eligible: false,
  promotion_reason_codes: ["v021_six_cell_qualification_bundle_missing"],
  qualification_bundle_artifact_id: null,
};
const productCandidate = {
  activated_at: "2026-01-10T00:00:00Z", asset_context_key: "us_large_cap",
  enrollment_id: "00000000-0000-0000-0000-000000000201", health: "observing",
  latest_as_of_session: null, latest_metrics: {}, lifecycle: "active",
  model_preset_key: "linear_weighted__signal_equal_v1", monitoring_start_at: "2026-01-10T00:00:00Z",
  name: "Momentum research candidate", open_alert_count: 0, primary_nav: null,
  product_artifact_id: "00000000-0000-0000-0000-000000000202", product_key: "candidate-a",
  qualification_artifact_id: "00000000-0000-0000-0000-000000000203", revision: 1,
  strategy_family_key: "us_large_cap_top_k", strategy_preset_key: "us_large_cap_top_k__k10",
  stress_nav: null, updated_at: "2026-01-10T00:00:00Z", version_number: 1,
  warning_codes: ["candidate_exploratory_suite"],
};
const productCatalog = { context: health.context, quality: health.quality, items: [productCandidate] };
const productDetail = {
  context: health.context, quality: health.quality, candidate: productCandidate,
  alerts: [], events: [], note: null, qualification_backtest: null,
  qualification_gate_results: {}, research_chain: null, reviews: [],
  selection_reason: "Promising exploratory result", snapshots: [],
  oos_window: { frozen_anchor_session: "2026-01-09", activation_session: "2026-01-10",
    latest_published_data_session: "2026-01-09", latest_published_data_known_at: "2026-01-09T21:00:00Z",
    latest_snapshot_session: null, post_freeze_session_count: 0, prospective_oos_session_count: 0,
    status: "awaiting_post_freeze_data", reason_codes: ["published_data_has_not_passed_frozen_anchor"] },
};
const productRecommendation = {
  context: health.context, quality: health.quality, available: true, coverage_ratio: 1,
  data_as_of_session: "2026-01-09", data_bundle_artifact_id: "00000000-0000-0000-0000-000000000204",
  data_known_at: "2026-01-09T21:00:00Z", decision_session: "2026-01-09",
  eligible_count: 100, frequency: "weekly", next_expected_signal_session: "2026-01-16",
  not_oos: true, positions: [{ allocation_role: "risk", asset_key: "aapl", model_score: 0.8,
    name: "Apple Inc.", rank: 1, retained_by_buffer: false, symbol: "AAPL", target_weight: 0.1 }],
  rankable_count: 100, reason_codes: [], recommended_execution_session: "2026-01-12",
  refresh_policy: "latest_published_signal", status: "accepted",
};
const productRanking = {
  context: health.context, quality: health.quality,
  cohorts: [{ artifact_id: "00000000-0000-0000-0000-000000000070",
    cohort_key: "weekly-full-5bps", version_number: 1, name: "Weekly Full 5 bps",
    description: "Strict context", context_fingerprint: "d".repeat(64), template_key: "full_history",
    initialization_policy: "carry_in", as_of_date: "2026-01-09", common_data_ready_date: "2025-01-01",
    common_simulation_start: "2025-01-02", common_metric_start: "2025-01-02",
    common_metric_end: "2026-01-09", currency: "USD", member_count: 1,
    benchmark_key: "spy_buy_and_hold", cost_bps_per_side: 5, required_warmup_observations: 253 }],
  active_cohort_artifact_id: "00000000-0000-0000-0000-000000000070",
  selected_metric: "net_sharpe", ranking_direction: "higher_is_better",
  candidate_count: 1, ranked_count: 1,
  entries: [{ rank: 1, result_artifact_id: "00000000-0000-0000-0000-000000000062",
    product_artifact_id: "00000000-0000-0000-0000-000000000045", product_key: "product-a",
    model_specification_key: "dimension_equal_weight__momentum_trend",
    variant_key: "top_k_equal_weight__k2", target_k: 2, frequency: "weekly",
    metric_value: 1.1, value_status: "defined", reason_code: null, observation_count: 252,
    core_metrics: { "strategy.cagr": 0.12, "relative.annualized_relative_wealth_growth": 0.03,
      "strategy.maximum_drawdown": -0.08 } }],
};
const productCompare = {
  context: health.context, quality: health.quality, mode: "controlled",
  changed_dimensions: ["k"], blocking_context_fields: [],
  entries: [1, 2].map((targetK, index) => ({
    result_artifact_id: `00000000-0000-0000-0000-00000000008${index}`,
    product_key: `product-${targetK}`, model_specification_key: "dimension_equal_weight__momentum_trend",
    strategy_template_key: "top_k_equal_weight", variant_key: `top_k_equal_weight__k${targetK}`,
    target_k: targetK, frequency: "weekly", cost_bps_per_side: 5, template_key: "full_history",
    initialization_policy: "carry_in", availability_status: "eligible", quality_status: "normal",
    resolved_start: "2025-01-02", resolved_end: "2026-01-09",
    metrics: [{ series_role: "strategy", metric_scope: "absolute", metric_key: "cagr", name: "CAGR",
      unit: "annual_ratio", value: 0.1 + index * 0.02, value_status: "defined", reason_code: null,
      observation_count: 252 }],
  })),
};
const decisionExplorer = {
  context: health.context, quality: health.quality,
  result_artifact_id: "00000000-0000-0000-0000-000000000062",
  target_path_artifact_id: "00000000-0000-0000-0000-000000000046",
  model_dataset_artifact_id: "00000000-0000-0000-0000-000000000047",
  model_specification_artifact_id: "00000000-0000-0000-0000-000000000038",
  universe_artifact_id: "00000000-0000-0000-0000-000000000048",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000049",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000050",
  model_method_key: "weighted_mean", available_dates: ["2026-01-09"], selected_date: "2026-01-09",
  target_k: 2, actual_holding_count: 2, reserve_target_weight: 0,
  positions: [{ asset_key: "iwd", symbol: "IWD", selected: true, model_score: 0.8, model_rank: 1,
    trend_state: "positive", target_weight: 0.5, decision_reason: "selected_by_rank",
    components: [{ dimension_key: "momentum_trend", dimension_weight: 1,
      signal_key: "return_continuation", signal_version_artifact_id: "00000000-0000-0000-0000-000000000091",
      signal_dataset_artifact_id: "00000000-0000-0000-0000-000000000092", signal_score: 0.8,
      signal_state: "positive", input_transform: "identity", component_weight: 1,
      transformed_signal_score: 0.8, weighted_component_input: 0.8, overall_contribution: 0.8,
      factor_key: "total_return", factor_variant_key: "total_return__w252",
      factor_dataset_artifact_id: "00000000-0000-0000-0000-000000000093", factor_value: 0.12,
      data_bundle_artifact_id: "00000000-0000-0000-0000-000000000049" }] }],
};

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="current-location" hidden>{location.pathname}{location.search}{location.hash}</span>;
}

function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><LocationProbe /><AppRoutes /></MemoryRouter></QueryClientProvider>);
}

let workspaceDraftFixture: Record<string, unknown> | null = null;
let productDetailFailuresRemaining = 0;
let experimentOverviewFixture: Record<string, unknown> | null = null;
let suiteStatusFixture: Record<string, unknown> | null = null;
let graphSuiteListFixture: Record<string, unknown> | null = null;
let graphSuiteStatusFixture: Record<string, unknown> | null = null;
let graphSuiteResultsFixture: Record<string, unknown> | null = null;
let graphSuiteLaunchBatchFixture: Record<string, unknown> | null = null;
let workspaceOptionsFixture: Record<string, unknown> | null = null;
let releaseControlFixture: Record<string, unknown> | null = hiddenReleaseControl;
let requestedSuiteStatusIds: string[] = [];

beforeEach(async () => {
  window.localStorage.clear();
  workspaceDraftFixture = null;
  productDetailFailuresRemaining = 0;
  experimentOverviewFixture = null;
  suiteStatusFixture = null;
  graphSuiteListFixture = null;
  graphSuiteStatusFixture = null;
  graphSuiteResultsFixture = null;
  graphSuiteLaunchBatchFixture = null;
  workspaceOptionsFixture = null;
  releaseControlFixture = hiddenReleaseControl;
  requestedSuiteStatusIds = [];
  await i18n.changeLanguage("zh-CN");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/v2/session")) {
      return new Response(JSON.stringify({
        context: health.context,
        quality: health.quality,
        actor_key: "local",
        roles: ["researcher", "operator"],
        authentication_source: "trusted_local_config",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith("/api/v2/release-control")) {
      if (!releaseControlFixture) {
        return new Response(JSON.stringify({ message: "Release control unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify(releaseControlFixture), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/v2/workspace/graph-drafts/by-key/")) {
      return new Response(JSON.stringify({
        message: "Graph Draft key not found",
        code: "graph_draft_key_not_found",
      }), { status: 404, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith("/api/v2/workspace/graph-drafts") && init?.method === "POST") {
      return new Response(JSON.stringify(graphDraftSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/workspace/drafts/")) {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        return new Response(JSON.stringify({
          context: health.context, quality: health.quality,
          research_draft_id: "00000000-0000-0000-0000-000000000099",
          researcher_id: "local", draft_key: "default", name: body.name,
          revision: 1, selection: body.selection, last_compiled_artifact_id: null,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (workspaceDraftFixture) return new Response(JSON.stringify(workspaceDraftFixture), { status: 200, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify({ detail: "draft not found" }), { status: 404, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith("/api/v2/workspace/suites") && init?.method === "POST") {
      return new Response(JSON.stringify({
        context: health.context, quality: health.quality,
        research_suite_id: "00000000-0000-0000-0000-000000000101",
        suite_artifact_id: "00000000-0000-0000-0000-000000000102",
        suite_key: "suite__test", suite_fingerprint: "test", predictive_cell_count: 1,
        portfolio_cell_count: 6, queued_work_item_count: 7, reused: false,
        suite_mode: "exploratory",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/v2/workspace/graph-suites?")) {
      return new Response(JSON.stringify(graphSuiteListFixture ?? {
        context: health.context, quality: health.quality, contract_version: "v0.22.0",
        items: [], total_count: 0, limit: 50, offset: 0,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/v2/workspace/graph-suite-launch-batches/")) {
      return new Response(JSON.stringify(graphSuiteLaunchBatchFixture ?? {
        message: "Launch batch not found",
      }), {
        status: graphSuiteLaunchBatchFixture ? 200 : 404,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/api/v2/workspace/graph-suites/") && url.endsWith("/results")) {
      return new Response(JSON.stringify(graphSuiteResultsFixture), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/v2/workspace/graph-suites/")) {
      return new Response(JSON.stringify(graphSuiteStatusFixture), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/api/v2/workspace/suites/")) {
      requestedSuiteStatusIds.push(url.split("/api/v2/workspace/suites/")[1].split(/[/?]/)[0]);
      return new Response(JSON.stringify(suiteStatusFixture ?? {
        context: health.context, quality: health.quality,
        research_suite_id: "00000000-0000-0000-0000-000000000101",
        total: 7, terminal: 2, complete: false,
        status_counts: { accepted: 2, queued: 5 }, suite_mode: "exploratory",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith("/api/v2/products")) {
      return new Response(JSON.stringify(productCatalog), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith(`/api/v2/products/${productCandidate.enrollment_id}/recommendation`)) {
      return new Response(JSON.stringify(productRecommendation), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith(`/api/v2/products/${productCandidate.enrollment_id}`)) {
      if (productDetailFailuresRemaining > 0) {
        productDetailFailuresRemaining -= 1;
        return new Response(JSON.stringify({ message: "Product detail temporarily unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify(productDetail), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    const releaseGates = { formal_enabled: false, product_enabled: false, reason_codes: ["pit_universe_gate_open", "terminal_event_gate_open", "impact_policy_gate_open"] };
    const payload = url.includes("/release-gates") ? releaseGates : url.includes("/workspace/options") ? (workspaceOptionsFixture ?? workspaceOptions) : url.includes("/catalog/assets/") && url.includes("/series") ? assetSeries : url.includes("/catalog/assets") ? assetCatalog : url.includes("compare/products") ? productCompare : url.includes("/decisions") ? decisionExplorer : url.includes("rankings/products") ? productRanking : url.includes("experiments/results") ? experimentResult : url.includes("experiments/overview") && url.includes("template_key=predictive_diagnostic") ? { ...experimentOverview, specifications: [], filtered_specification_count: 0 } : url.includes("experiments/overview") ? (experimentOverviewFixture ?? experimentOverview) : url.includes("strategies/targets") ? strategyTarget : url.includes("strategies/overview") ? strategyOverview : url.includes("models/overview") ? modelOverview : url.includes("signals/overview") ? signalOverview : url.includes("factors/overview") ? factorOverview : url.includes("data/overview") ? dataOverview : url.includes("health") ? health : url.includes("capabilities") ? capabilities : artifacts;
    return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
  });
});

test("opens the current v0.22 research assets as the default page", async () => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "explicit_eligible",
    transition_sequence: 2,
    v022_explicit_creation_allowed: true,
  };
  renderRoute("/?lang=zh-CN");
  await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent("/research-context"));
  expect(await screen.findByRole("heading", { name: "研究资产" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "加工层 1" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "因子" })).not.toBeInTheDocument();
});

test.skip("retired v0.21 Workspace route", async () => {
  renderRoute("/?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "定向研究工作台" })).toBeInTheDocument();
  expect(screen.getByText("20260802_02_v02_lineage")).toBeInTheDocument();
  expect(screen.getByText("US Style Rotation 4 ETF Sample v1")).toBeInTheDocument();
  expect(screen.getByText("候鸟实验室")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "数据" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "因子" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "加工层 1" })).not.toBeInTheDocument();
});

test("hidden release shows the split v0.22 navigation without creating a Graph Draft", async () => {
  renderRoute("/workspace-v022?lang=zh-CN&frequency=weekly");

  expect(await screen.findByRole("heading", { name: "v0.22 研究编辑尚未开放" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "加工层 1" })).toHaveAttribute("href", expect.stringContaining("/workspace-v022/processing-1"));
  expect(screen.getByRole("link", { name: "加工层 2" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "加工层 3" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "聚合层" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "检查并编译" })).not.toBeInTheDocument();
  expect(screen.queryByText("三层加工工作台")).not.toBeInTheDocument();
  const graphCreates = vi.mocked(globalThis.fetch).mock.calls.filter(([input, init]) => (
    String(input).endsWith("/api/v2/workspace/graph-drafts") && init?.method === "POST"
  ));
  expect(graphCreates).toHaveLength(0);
});

test("default v0.22 contract replaces the legacy Factor Signal Model navigation", async () => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "default",
    default_contract: "v0.22",
    transition_sequence: 3,
    v021_research_creation_allowed: false,
    v022_explicit_creation_allowed: true,
  };
  renderRoute("/experiments?lang=zh-CN");

  expect(await screen.findByRole("heading", { name: "实验排行榜与运行历史" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "加工层 1" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "加工层 2" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "加工层 3" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "聚合层" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "检查并编译" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "因子" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "信号" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "模型" })).not.toBeInTheDocument();
});

test("sidebar navigation preserves the active launch batch and drops unrelated page filters", async () => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "explicit_eligible",
    transition_sequence: 2,
    v022_explicit_creation_allowed: true,
  };
  const batchId = "6377a984-e829-51f4-becd-d43235d448a9";
  graphSuiteLaunchBatchFixture = {
    context: health.context,
    quality: health.quality,
    contract_version: "v0.22.0",
    suite_launch_batch_id: batchId,
    source_graph_draft_id: graphDraftSnapshot.graph_draft_id,
    source_graph_draft_revision: graphDraftSnapshot.revision,
    batch_fingerprint: "8".repeat(64),
    status: "running",
    stage: "complete",
    failed_frequency: null,
    failure_code: null,
    failure_summary: null,
    children: [],
    reused: true,
  };
  renderRoute(`/research-context?lang=zh-CN&frequency=monthly&launch_batch=${batchId}&graph_suite=ignored&sort=sharpe_ratio`);

  expect(await screen.findByRole("heading", { name: "研究资产" })).toBeInTheDocument();
  const experimentLink = screen.getByRole("link", { name: "实验" });
  const href = experimentLink.getAttribute("href") ?? "";
  expect(href).toContain(`/experiments?`);
  expect(href).toContain(`launch_batch=${batchId}`);
  expect(href).toContain("frequency=monthly");
  expect(href).not.toContain("graph_suite");
  expect(href).not.toContain("sort");

  fireEvent.click(experimentLink);
  await waitFor(() => expect(screen.getByTestId("current-location"))
    .toHaveTextContent(`/experiments?lang=zh-CN&frequency=monthly&contract=v0.22&launch_batch=${batchId}`));
  fireEvent.click(screen.getByRole("link", { name: "资产" }));
  await waitFor(() => expect(screen.getByTestId("current-location"))
    .toHaveTextContent(`/research-context?lang=zh-CN&frequency=monthly&contract=v0.22&launch_batch=${batchId}`));
  fireEvent.click(screen.getByRole("link", { name: "实验" }));
  await waitFor(() => expect(screen.getByTestId("current-location"))
    .toHaveTextContent(`launch_batch=${batchId}`));
});

test("v0.22 experiment reads typed Suite results without querying the v0.21 overview", async () => {
  await i18n.changeLanguage("en");
  const suiteId = "00000000-0000-4000-8000-000000000401";
  graphSuiteStatusFixture = {
    context: health.context, quality: health.quality, contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "00000000-0000-4000-8000-000000000402",
    status: "completed", total: 4, terminal: 4, complete: true,
    status_counts: { completed: 4 }, suite_mode: "exploratory",
  };
  graphSuiteResultsFixture = {
    context: health.context, quality: health.quality, contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "00000000-0000-4000-8000-000000000402",
    status: "completed", complete: true, expected_result_count: 1, result_count: 1,
    results: [{
      research_cell_id: "00000000-0000-4000-8000-000000000403",
      research_suite_branch_id: "00000000-0000-4000-8000-000000000404",
      compiled_strategy_branch_id: "00000000-0000-4000-8000-000000000405",
      configuration_snapshot_id: "00000000-0000-4000-8000-000000000406",
      portfolio_evaluation_data_context_id: "00000000-0000-4000-8000-000000000407",
      result_artifact_id: "00000000-0000-4000-8000-000000000408",
      payload_manifest_id: "00000000-0000-4000-8000-000000000409",
      payload_manifest_artifact_id: "00000000-0000-4000-8000-000000000410",
      result_fingerprint: "a".repeat(64), logical_payload_fingerprint: "b".repeat(64),
      manifest_hash: "c".repeat(64), outcome: "accepted", quality_status: "passed",
      effective_start: "2024-01-02", effective_end: "2024-12-31",
      metric_document: {}, result_document: {},
      diagnostic: {
        metrics: [{ metric_group: "absolute", metric_key: "cagr", value: "0.12", value_status: "defined", reason_code: null, observation_count: 252 }],
        quality: { outcome: "accepted", status: "passed", reason_code: null, details: {}, path_session_count: 252 },
        execution: {
          benchmark_asset_id: "00000000-0000-4000-8000-000000000411", benchmark_asset_key: "spy",
          cost_policy_key: "linear_bps", basis_points_per_side: "5", execution_delay_sessions: 1,
          evaluation_input_cutoff_at: "2024-12-31T21:00:00Z", work_execution_fingerprint: "d".repeat(64),
          evaluation_data_context_fingerprint: "e".repeat(64),
        },
        evidence: {
          publication_status: "published", result_evidence_snapshot_id: "00000000-0000-4000-8000-000000000412",
          result_evidence_artifact_id: "00000000-0000-4000-8000-000000000413", evidence_fingerprint: "f".repeat(64),
          evidence_class: "locked_historical_test", common_evaluation_panel_id: "00000000-0000-4000-8000-000000000414",
          common_evaluation_panel_fingerprint: "1".repeat(64),
        },
        elements: [{
          result_element_diagnostic_id: "00000000-0000-4000-8000-000000000422",
          artifact_id: "00000000-0000-4000-8000-000000000423",
          diagnostic_fingerprint: "4".repeat(64),
          diagnostic_document: {
            compiled_feature_occurrence_id: "00000000-0000-4000-8000-000000000424",
            feature_variant_key: "lagged_return__w20", stage_no: 1,
            payload_manifest_id: "00000000-0000-4000-8000-000000000425",
            manifest_artifact_id: "00000000-0000-4000-8000-000000000426",
            manifest_hash: "5".repeat(64), research_direction: "unsigned",
            target_key: "weekly_next_open_to_next_open",
            target_version_id: "00000000-0000-4000-8000-000000000420",
            target_version_artifact_id: "00000000-0000-4000-8000-000000000421",
            frequency: "weekly", coverage_start: "2024-01-05", coverage_end: "2024-12-27",
            expected_observation_count: 208, observed_value_count: 208,
            missing_value_count: 0, evaluation_period_count: 52, valid_ic_count: 0,
            metrics: [
              { metric_key: "coverage_ratio", value: "1", reason_code: null },
              { metric_key: "mean_rank_ic", value: null, reason_code: "rank_ic_unavailable" },
            ],
          },
        }, {
          result_element_diagnostic_id: "00000000-0000-4000-8000-000000000415",
          artifact_id: "00000000-0000-4000-8000-000000000416",
          diagnostic_fingerprint: "2".repeat(64),
          diagnostic_document: {
            compiled_feature_occurrence_id: "00000000-0000-4000-8000-000000000417",
            feature_variant_key: "return_continuation__w20", stage_no: 3,
            payload_manifest_id: "00000000-0000-4000-8000-000000000418",
            manifest_artifact_id: "00000000-0000-4000-8000-000000000419",
            manifest_hash: "3".repeat(64), research_direction: "positive",
            target_key: "weekly_next_open_to_next_open",
            target_version_id: "00000000-0000-4000-8000-000000000420",
            target_version_artifact_id: "00000000-0000-4000-8000-000000000421",
            frequency: "weekly", coverage_start: "2024-01-05", coverage_end: "2024-12-27",
            expected_observation_count: 208, observed_value_count: 198,
            missing_value_count: 10, evaluation_period_count: 52, valid_ic_count: 50,
            metrics: [
              { metric_key: "coverage_ratio", value: "0.95", reason_code: null },
              { metric_key: "mean_rank_ic", value: "0.08", reason_code: null },
            ],
          },
        }],
      },
    }],
  };

  renderRoute(`/experiments?graph_suite=${suiteId}&lang=en`);

  expect(await screen.findByRole("heading", { name: "v0.22 strategy experiment" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Weekly" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "This run" }));
  expect(await screen.findByRole("heading", { name: "Portfolio Cell results" })).toBeInTheDocument();
  expect(screen.getByText("12%")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Element diagnostics" }));
  expect(screen.getByRole("heading", { name: "Processing stage 1 diagnostics" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Processing stage 3 diagnostics" })).toBeInTheDocument();
  expect(screen.getByText("50 / 52")).toBeInTheDocument();
  expect(screen.getByText("95%")).toBeInTheDocument();
  expect(screen.getByText(/They are non-causal/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Data quality" }));
  expect(screen.getByText("252")).toBeInTheDocument();
  expect(screen.getByText("spy")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Evidence & lineage" }));
  expect(screen.getByText("locked_historical_test")).toBeInTheDocument();
  expect(vi.mocked(globalThis.fetch).mock.calls.some(([input]) => (
    String(input).includes("/experiments/overview")
  ))).toBe(false);
});

test("experiment index lists persisted v0.22 Suites without querying the v0.21 overview", async () => {
  const suiteId = "00000000-0000-4000-8000-000000000421";
  graphSuiteListFixture = {
    context: health.context, quality: health.quality, contract_version: "v0.22.0",
    total_count: 1, limit: 50, offset: 0,
    items: [{
      research_suite_id: suiteId,
      compiled_research_graph_id: "00000000-0000-4000-8000-000000000422",
      graph_fingerprint: "d".repeat(64), suite_fingerprint: "e".repeat(64),
      status: "completed", total: 4, terminal: 4, complete: true,
      status_counts: { completed: 4 }, strategy_branch_count: 1,
      backtest_cell_count: 1, suite_mode: "exploratory",
      created_at: "2026-08-13T12:00:00Z",
    }],
  };

  renderRoute("/experiments?lang=zh-CN");

  fireEvent.click(await screen.findByRole("button", { name: "运行与历史" }));
  expect(await screen.findByRole("heading", { name: "v0.22 实验历史" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看结果 →" })).toHaveAttribute(
    "href", `/experiments?graph_suite=${suiteId}`,
  );
  expect(screen.getByText("共 1 个实验；列表来自后端持久身份，不依赖当前浏览器。")).toBeInTheDocument();
  expect(vi.mocked(globalThis.fetch).mock.calls.some(([input]) => (
    String(input).includes("/experiments/overview")
  ))).toBe(false);
});

test.each([
  ["/research-review?lang=zh-CN&frequency=weekly", "/strategy-configuration?lang=zh-CN&frequency=weekly#configuration-review"],
  ["/workspace-v022/review?lang=zh-CN&frequency=weekly&contract=v0.22", "/workspace-v022/strategy?lang=zh-CN&frequency=weekly&contract=v0.22#configuration-review"],
])("legacy review route %s redirects inside the same Graph workflow", async (source, target) => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "explicit_eligible",
    transition_sequence: 2,
    v022_explicit_creation_allowed: true,
  };
  renderRoute(source);

  await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent(target));
  await waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.filter(([input, init]) => (
    String(input).endsWith("/api/v2/workspace/graph-drafts") && init?.method === "POST"
  ))).toHaveLength(1));
});

test.skip("superseded split-contract release failure presentation", async () => {
  releaseControlFixture = null;
  renderRoute("/?lang=zh-CN");

  expect(await screen.findByRole("heading", { name: "无法验证研究发布状态" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "因子" })).not.toBeInTheDocument();
  expect(vi.mocked(globalThis.fetch).mock.calls.some(([input]) => String(input).includes("/workspace/drafts/"))).toBe(false);
});

test.skip("superseded split-contract maintenance presentation", async () => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "maintenance_read_only",
    transition_sequence: 4,
    maintenance_read_only: true,
    v021_research_creation_allowed: false,
    v022_explicit_creation_allowed: false,
  };
  renderRoute("/?lang=zh-CN");

  expect(await screen.findByRole("heading", { name: "系统处于维护只读状态" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "因子" })).not.toBeInTheDocument();
  expect(vi.mocked(globalThis.fetch).mock.calls.some(([input]) => String(input).includes("/workspace/drafts/"))).toBe(false);
});

test.each([
  ["/assets?lang=en", "/research-context"],
  ["/factors?lang=en", "/processing-1"],
  ["/models?lang=en", "/aggregation"],
  ["/strategies?lang=en", "/strategy-configuration"],
])("retired editable route %s redirects into the current workflow", async (source, target) => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "explicit_eligible",
    transition_sequence: 2,
    v022_explicit_creation_allowed: true,
  };
  renderRoute(source);
  await waitFor(() => expect(screen.getByTestId("current-location")).toHaveTextContent(target));
});

test.skip("superseded legacy deep-link blocker page", async () => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "default",
    default_contract: "v0.22",
    transition_sequence: 3,
    v021_research_creation_allowed: false,
    v022_explicit_creation_allowed: true,
  };
  renderRoute("/factors?lang=zh-CN");

  expect(await screen.findByRole("heading", { name: "v0.21 研究编辑已关闭" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "因子库与参数选择" })).not.toBeInTheDocument();
  expect(vi.mocked(globalThis.fetch).mock.calls.some(([input]) => String(input).includes("/factors/overview"))).toBe(false);
});

test.skip("superseded split-contract query selection", async () => {
  renderRoute("/experiments?lang=zh-CN&contract=v0.22");

  expect(await screen.findByRole("heading", { name: "策略实验与可追溯绩效" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "因子" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "加工层 1" })).not.toBeInTheDocument();
});

test.skip("retired legacy Workspace options", async () => {
  await i18n.changeLanguage("en");
  const legacyOptions: Record<string, unknown> = { ...workspaceOptions };
  delete legacyOptions.asset_data_input_blockers;
  workspaceOptionsFixture = legacyOptions;
  workspaceDraftFixture = {
    context: health.context, quality: health.quality,
    research_draft_id: "00000000-0000-0000-0000-000000000099",
    researcher_id: "local", draft_key: "default", name: "Legacy local draft", revision: 88,
    selection: { frequency: "weekly", asset_security_ids: ["00000000-0000-0000-0000-000000000022"], factor_variant_keys: [], signal_version_keys: [], model_preset_keys: [], model_target_keys: ["cross_sectional_relative_return__h5"], strategy_preset_keys: [] },
    last_compiled_artifact_id: null,
  };

  renderRoute("/?lang=en");

  expect(await screen.findByRole("heading", { name: "Targeted research workspace" })).toBeInTheDocument();
  expect(screen.getByText("Invalid selections").nextElementSibling).toHaveTextContent("0");
});

test.skip("retired legacy exploratory submission", async () => {
  workspaceDraftFixture = {
    context: health.context, quality: health.quality,
    research_draft_id: "00000000-0000-0000-0000-000000000099",
    researcher_id: "local", draft_key: "default", name: "Local research draft", revision: 3,
    selection: { frequency: "weekly", asset_security_ids: [], factor_variant_keys: [], signal_version_keys: [], model_preset_keys: [], model_target_keys: ["cross_sectional_relative_return__h5"], strategy_preset_keys: [] },
    last_compiled_artifact_id: null,
  };
  renderRoute("/?lang=zh-CN");
  fireEvent.click(await screen.findByRole("button", { name: "运行探索性实验" }));
  expect(await screen.findByRole("heading", { name: "策略实验与可追溯绩效" })).toBeInTheDocument();
  expect(await screen.findByRole("status")).toHaveTextContent("实验已排队");
  expect(screen.getByRole("status")).toHaveTextContent("2 / 7");
  expect(screen.getByRole("progressbar", { name: "回测进度" })).toHaveAttribute("aria-valuenow", "2");
});

test.skip("retired legacy browser draft reconciliation", async () => {
  const selectedId = "00000000-0000-0000-0000-000000000022";
  window.localStorage.setItem("style-rotation-v021-workspace-draft", JSON.stringify({
    revision: 6,
    selection: { assetSecurityIds: [], factorVariantKeys: [], signalVersionKeys: [], modelPresetKeys: [], modelTargetKeys: ["cross_sectional_relative_return__h5"], strategyPresetKeys: [], frequency: "weekly" },
  }));
  workspaceDraftFixture = {
    context: health.context, quality: health.quality,
    research_draft_id: "00000000-0000-0000-0000-000000000099",
    researcher_id: "local", draft_key: "default", name: "Local research draft", revision: 7,
    selection: { frequency: "weekly", asset_security_ids: [selectedId], factor_variant_keys: [], signal_version_keys: [], model_preset_keys: [], model_target_keys: ["cross_sectional_relative_return__h5"], strategy_preset_keys: [] },
    last_compiled_artifact_id: null,
  };
  renderRoute("/?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "定向研究工作台" })).toBeInTheDocument();
  await vi.waitFor(() => expect(document.querySelector(".workspace-stage-card .workspace-stage-state strong")?.textContent).toBe("1"));
});

test.skip("retired legacy local-storage draft", async () => {
  const selectedId = "00000000-0000-0000-0000-000000000022";
  const localSelection = {
    assetSecurityIds: [selectedId],
    assetDataInputs: { [selectedId]: ["canonical_market_bars"] },
    factorVariantKeys: ["total_return__w120"],
    signalVersionKeys: ["return_continuation__total_return__w120"],
    modelPresetKeys: ["single_signal__identity_v1"],
    modelTargetKeys: ["cross_sectional_relative_return__h5"],
    strategyPresetKeys: ["multi_etf_top_k__k2__none__none__none"],
    frequency: "weekly",
  };
  window.localStorage.setItem("style-rotation-v021-workspace-draft", JSON.stringify({
    revision: 7,
    selection: localSelection,
  }));
  workspaceDraftFixture = {
    context: health.context, quality: health.quality,
    research_draft_id: "00000000-0000-0000-0000-000000000099",
    researcher_id: "local", draft_key: "default", name: "Local research draft", revision: 7,
    selection: {
      frequency: "weekly",
      asset_security_ids: [selectedId],
      asset_data_inputs: { [selectedId]: ["canonical_market_bars"] },
      factor_variant_keys: ["total_return__w120"],
      signal_version_keys: ["return_continuation__total_return__w120"],
      model_preset_keys: ["single_signal__identity_v1"],
      model_target_keys: ["cross_sectional_relative_return__h5"],
      strategy_preset_keys: ["multi_etf_top_k__k2__none__none__none"],
    },
    last_compiled_artifact_id: null,
  };

  renderRoute("/?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "定向研究工作台" })).toBeInTheDocument();
  await new Promise((resolve) => window.setTimeout(resolve, 1_100));

  const draftWrites = vi.mocked(globalThis.fetch).mock.calls.filter(([input, init]) => (
    String(input).includes("/workspace/drafts/") && init?.method === "PUT"
  ));
  expect(draftWrites).toHaveLength(0);
});

test("language switch keeps the current asset route and translates its heading", async () => {
  releaseControlFixture = {
    ...hiddenReleaseControl,
    state: "explicit_eligible",
    transition_sequence: 2,
    v022_explicit_creation_allowed: true,
  };
  renderRoute("/research-context?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "研究资产" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByRole("heading", { name: "Research assets" })).toBeInTheDocument();
  expect(screen.getByTestId("current-location")).toHaveTextContent("/research-context");
});

test("mobile navigation closes after selecting a current route", async () => {
  await i18n.changeLanguage("en");
  vi.spyOn(api, "v022Products").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    items: [],
  });
  renderRoute("/experiments?lang=en");
  expect(await screen.findByRole("heading", { name: "Experiment leaderboard and run history" })).toBeInTheDocument();
  const open = screen.getByRole("button", { name: "Open menu" });
  fireEvent.click(open);
  expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(screen.getByRole("link", { name: "Products" }));
  expect(await screen.findByRole("heading", { name: "Research candidate Products" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "false");
});

test.skip("retired factor-page language switch", async () => {
  renderRoute("/factors?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "因子库与参数选择" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByRole("heading", { name: "Factor catalog and parameter choices" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Factor families and parameter choices" })).toBeInTheDocument();
});

test.skip("retired legacy mobile route selection", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/factors?lang=en&frequency=weekly");
  expect(await screen.findByRole("heading", { name: "Factor catalog and parameter choices" })).toBeInTheDocument();
  const open = screen.getByRole("button", { name: "Open menu" });
  expect(open).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(open);
  expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(screen.getByRole("link", { name: "Models" }));
  expect(await screen.findByRole("heading", { name: "Model structures and input contracts" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "false");
});

test("Runs queries only v0.21 research Suite IDs and keeps progress bilingual", async () => {
  const researchSuiteId = "00000000-0000-0000-0000-000000000301";
  const suiteArtifactId = "00000000-0000-0000-0000-000000000302";
  experimentOverviewFixture = {
    ...experimentOverview,
    suites: [
      experimentOverview.suites[0],
      { artifact_id: suiteArtifactId, research_suite_id: researchSuiteId, suite_key: "v021-targeted",
        version_number: 2, name: "Targeted Suite", description: "v0.21 queue fixture", specification_count: 7 },
    ],
  };
  suiteStatusFixture = {
    context: health.context, quality: health.quality, research_suite_id: researchSuiteId,
    total: 7, terminal: 3, complete: false,
    status_counts: { accepted: 2, running: 1, queued: 4 }, suite_mode: "exploratory",
  };

  renderRoute("/runs?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "运行记录" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Targeted Suite" })).toBeInTheDocument();
  expect(screen.queryByText("Formal v0.2")).not.toBeInTheDocument();
  expect(screen.getByText("研究 Suite ID")).toBeInTheDocument();
  expect(screen.getByText(researchSuiteId)).toBeInTheDocument();
  expect(screen.queryByText(suiteArtifactId)).not.toBeInTheDocument();
  expect(await screen.findByRole("progressbar", { name: "Targeted Suite 回测进度" })).toHaveAttribute("aria-valuenow", "3");
  expect(screen.getByText("已接受 2 · 运行中 1 · 排队 4")).toBeInTheDocument();
  await vi.waitFor(() => expect(requestedSuiteStatusIds).toEqual([researchSuiteId]));
  expect(screen.getByRole("link", { name: /查看实验与进度/ })).toHaveAttribute(
    "href", `/experiments?suite=${researchSuiteId}&lang=zh-CN`,
  );

  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByRole("heading", { name: "Runs" })).toBeInTheDocument();
  expect(screen.getByText("Visible Suites")).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "Targeted Suite backtest progress" })).toHaveAttribute("aria-valuenow", "3");
  expect(screen.getByText("accepted 2 · running 1 · queued 4")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open experiment and progress/ })).toHaveAttribute(
    "href", `/experiments?suite=${researchSuiteId}&lang=en`,
  );
});

test("artifact empty state is explicit", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/artifacts?lang=en");
  expect(await screen.findByText("No matching published data")).toBeInTheDocument();
});

test.skip("retired legacy data route", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/data?lang=en");
  expect(await screen.findByRole("heading", { name: "Targeted research workspace" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Data" })).not.toBeInTheDocument();
});

test.skip("retired legacy asset editor", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/assets?lang=en");
  expect(await screen.findByRole("heading", { name: "Assets and research capability" })).toBeInTheDocument();
  expect(screen.getByText("Apple Inc.")).toBeInTheDocument();
  expect(screen.getByText("Apple · APPL")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Stocks/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Apple Inc\./ }));
  expect(await screen.findByRole("heading", { name: "Adjusted price history" })).toBeInTheDocument();
  expect(screen.getByRole("dialog").parentElement?.parentElement).toBe(document.body);
  expect(screen.getByRole("link", { name: "Download cleaned CSV" })).toHaveAttribute(
    "href", "/api/v2/catalog/assets/00000000-0000-0000-0000-000000000022/download.csv",
  );
  const marketInput = screen.getByRole("checkbox", { name: /Canonical OHLCV/ });
  const fundamentalInput = screen.getByRole("checkbox", { name: /Filed fundamental facts/ });
  expect(marketInput).not.toBeChecked();
  expect(fundamentalInput).toBeDisabled();
  fireEvent.click(marketInput);
  expect(marketInput).toBeChecked();
  expect(JSON.parse(String(window.localStorage.getItem("style-rotation-v021-workspace-draft"))).selection.assetDataInputs).toEqual({
    "00000000-0000-0000-0000-000000000022": ["canonical_market_bars"],
  });
});

test.skip("retired legacy asset local-storage selection", async () => {
  await i18n.changeLanguage("en");
  const first = renderRoute("/assets?lang=en");
  expect(await screen.findByText("Apple Inc.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select" }));
  expect(JSON.parse(String(window.localStorage.getItem("style-rotation-v021-workspace-draft"))).selection.assetSecurityIds).toEqual([
    "00000000-0000-0000-0000-000000000022",
  ]);
  expect(JSON.parse(String(window.localStorage.getItem("style-rotation-v021-workspace-draft"))).selection.assetDataInputs).toEqual({
    "00000000-0000-0000-0000-000000000022": ["canonical_market_bars"],
  });
  fireEvent.click(screen.getByRole("link", { name: "Workspace" }));
  expect(await screen.findByRole("heading", { name: "Targeted research workspace" })).toBeInTheDocument();
  expect(document.querySelector(".workspace-stage-card .workspace-stage-state strong")?.textContent).toBe("1");
  first.unmount();

  renderRoute("/?lang=en");
  expect(await screen.findByRole("heading", { name: "Targeted research workspace" })).toBeInTheDocument();
  expect(document.querySelector(".workspace-stage-card .workspace-stage-state strong")?.textContent).toBe("1");
});

test("asset catalog loads every API page before offering filtered select-all", async () => {
  const base = assetCatalog.items[0];
  const firstPage = Array.from({ length: 200 }, (_, index) => ({
    ...base,
    security_id: `00000000-0000-0000-0001-${String(index).padStart(12, "0")}`,
    symbol: `S${index}`,
  }));
  const last = {
    ...base,
    security_id: "00000000-0000-0000-0002-000000000000",
    symbol: "LAST",
  };
  vi.mocked(globalThis.fetch).mockImplementation(async (input) => {
    const offset = Number(new URL(String(input), "http://localhost").searchParams.get("offset") ?? 0);
    return new Response(JSON.stringify({
      ...assetCatalog,
      total: 201,
      items: offset === 0 ? firstPage : [last],
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const result = await api.allAssets();
  expect(result.items).toHaveLength(201);
  expect(result.items.at(-1)?.symbol).toBe("LAST");
  expect(globalThis.fetch).toHaveBeenCalledTimes(2);
});

test.skip("retired legacy factor editor", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/factors?lang=en");
  expect((await screen.findAllByText("total_return__w120")).length).toBeGreaterThan(0);
  expect(screen.queryByText("Redundancy alerts")).not.toBeInTheDocument();
  expect(screen.queryByText("ρ 0.91")).not.toBeInTheDocument();
  expect(screen.getAllByText("Pₜ ÷ Pₜ₋w − 1").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Show exact calculation definition").length).toBeGreaterThan(0);
  expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main-content");
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("checkbox")[0]);
  expect(screen.getByRole("heading", { name: "Factor catalog and parameter choices" })).toBeInTheDocument();
});

test.skip("retired legacy signal editor", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/signals?lang=en&frequency=weekly");
  expect(await screen.findByRole("heading", { name: "Signal catalog and legal inputs" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Signal families and parameter versions" })).toBeInTheDocument();
  expect(screen.getAllByText("return_continuation").length).toBeGreaterThan(0);
  expect(screen.queryByText("Mean Rank IC")).not.toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test.skip("retired legacy model editor", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/models?lang=en&frequency=weekly");
  expect(await screen.findByRole("heading", { name: "Model structures and input contracts" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Model families and fixed presets" })).toBeInTheDocument();
  expect(screen.getByText("Prediction targets and horizons")).toBeInTheDocument();
  expect(screen.getByText("Cross-sectional relative future return")).toBeInTheDocument();
  expect(screen.queryByText("Controlled dimension ablation")).not.toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test.skip("retired legacy strategy editor", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/strategies?lang=en");
  expect(await screen.findByRole("heading", { name: "Strategy rules and fixed parameters" })).toBeInTheDocument();
  expect(screen.getByText("Multi-ETF Cross-sectional Top-K Rotation")).toBeInTheDocument();
  expect(screen.getByText("multi_etf_top_k__k2__none__none__none")).toBeInTheDocument();
  expect(screen.queryByText("selected_by_rank")).not.toBeInTheDocument();
  expect(screen.queryByText(/Sharpe ratio/i)).not.toBeInTheDocument();
});

test.skip("retired legacy experiment results", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/experiments?version=legacy&lang=en");
  expect(await screen.findByRole("heading", { name: "Strategy experiments and traceable performance" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Predictive diagnostics" })).toBeInTheDocument();
  expect(screen.getByText(/Model outputs are evaluated period by period/)).toBeInTheDocument();
  expect(screen.getByText("12%")).toBeInTheDocument();
  expect(screen.getByText("9%")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Momentum and trend equal-weight model/ }));
  expect(await screen.findByText("All outputs published")).toBeInTheDocument();
  expect(screen.getByText("Complete performance metrics")).toBeInTheDocument();
  expect(screen.getByLabelText("Net wealth vs SPY benchmark")).toBeInTheDocument();
  expect(screen.getByLabelText("Excess wealth")).toBeInTheDocument();
  expect(screen.getAllByText("1.1").length).toBeGreaterThan(0);
  expect(await screen.findByRole("heading", { name: "Decision Explorer" })).toBeInTheDocument();
  expect(screen.getByText("total_return__w252")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Not currently eligible for research-candidate promotion" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Promote to Product candidate" })).toBeDisabled();
});

test.skip("retired legacy Suite query results", async () => {
  const researchSuiteId = "00000000-0000-0000-0000-000000000301";
  const failed = {
    ...experimentOverview.specifications[0],
    result_artifact_id: null,
    suite_mode: "exploratory",
    status: "failed",
    availability_status: null,
    quality_status: null,
    attempt_number: 3,
    error_summary: "Portfolio Cell is waiting for its Predictive Result",
    core_metrics: {},
  };
  experimentOverviewFixture = {
    ...experimentOverview,
    quality: { state: "warning", codes: ["experiment.failed_cells"] },
    accepted_count: 0,
    failed_count: 1,
    specifications: [failed],
  };

  renderRoute(`/experiments?suite=${researchSuiteId}&lang=zh-CN`);

  expect(await screen.findByText(/尝试 3 次 · Portfolio Cell is waiting/)).toBeInTheDocument();
  await vi.waitFor(() => expect(vi.mocked(globalThis.fetch).mock.calls.some(
    ([input]) => String(input).includes(
      `/api/v2/experiments/overview?research_suite_id=${researchSuiteId}`,
    ),
  )).toBe(true));
});

test.skip("retired legacy Workspace Product copy", async () => {
  renderRoute("/?lang=zh-CN");
  expect(await screen.findByText(/可升级为带警告的样本外研究候选/)).toBeInTheDocument();
  expect(screen.queryByText(/不能升级为Product/)).not.toBeInTheDocument();
});

test("legacy compare route opens the current v0.22 Product catalog", async () => {
  await i18n.changeLanguage("en");
  vi.spyOn(api, "v022Products").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    items: [],
  });
  renderRoute("/compare?lang=en");
  expect(await screen.findByRole("heading", { name: "Research candidate Products" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "No Product yet" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Compare" })).not.toBeInTheDocument();
});
