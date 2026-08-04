import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { AppRoutes } from "../App";
import i18n from "../i18n";

const health = {
  context: { api_version: "v2", system_version: "0.2.0", read_only: true },
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

function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter></QueryClientProvider>);
}

beforeEach(async () => {
  window.localStorage.clear();
  await i18n.changeLanguage("zh-CN");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const payload = url.includes("compare/products") ? productCompare : url.includes("/decisions") ? decisionExplorer : url.includes("rankings/products") ? productRanking : url.includes("experiments/results") ? experimentResult : url.includes("experiments/overview") ? experimentOverview : url.includes("strategies/targets") ? strategyTarget : url.includes("strategies/overview") ? strategyOverview : url.includes("models/overview") ? modelOverview : url.includes("signals/overview") ? signalOverview : url.includes("factors/overview") ? factorOverview : url.includes("data/overview") ? dataOverview : url.includes("health") ? health : url.includes("capabilities") ? capabilities : artifacts;
    return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
  });
});

test("renders real foundation data without inventing future research results", async () => {
  renderRoute("/?lang=zh-CN");
  expect(await screen.findByText("从可追溯研究走向严格策略比较")).toBeInTheDocument();
  expect(screen.getByText("20260802_02_v02_lineage")).toBeInTheDocument();
  expect(await screen.findByText("策略产品排行榜")).toBeInTheDocument();
  expect(screen.getByText("候鸟实验室")).toBeInTheDocument();
});

test("language switch keeps the route and translates fixed UI text", async () => {
  renderRoute("/factors?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "因子诊断与参数实例" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByRole("heading", { name: "Factor diagnostics and variants" })).toBeInTheDocument();
  expect(screen.getByText("Parameter stability")).toBeInTheDocument();
});

test("artifact empty state is explicit", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/artifacts?lang=en");
  expect(await screen.findByText("No matching published data")).toBeInTheDocument();
});

test("data page renders published diagnostics without strategy metrics", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/data?lang=en");
  expect(await screen.findByRole("heading", { name: "Data quality and availability" })).toBeInTheDocument();
  expect(screen.getByText("us_style_daily_bars")).toBeInTheDocument();
  expect(screen.getByText("IWD · 1,600")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("factor page displays measurement diagnostics without strategy performance", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/factors?lang=en");
  expect((await screen.findAllByText("total_return__w20")).length).toBeGreaterThan(0);
  expect(screen.getByText("Redundancy alerts")).toBeInTheDocument();
  expect(screen.getAllByText("ρ 0.91")).toHaveLength(2);
  expect(screen.getByText("Pₜ ÷ Pₜ₋w − 1")).toBeInTheDocument();
  expect(screen.getByText("Show exact calculation definition")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main-content");
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("signal page displays published directional diagnostics without strategy rankings", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/signals?lang=en&frequency=weekly");
  expect(await screen.findByRole("heading", { name: "Signal evaluation and economic direction" })).toBeInTheDocument();
  expect(screen.getAllByText("return_continuation__total_return__w252").length).toBeGreaterThan(0);
  expect(screen.getByText("Mean Rank IC")).toBeInTheDocument();
  expect(screen.getByText("Signal redundancy alerts")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("model page displays composition, controlled ablation, and no strategy ranking", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/models?lang=en&frequency=weekly");
  expect(await screen.findByRole("heading", { name: "Model structure and independent diagnostics" })).toBeInTheDocument();
  expect(screen.getAllByText("dimension_equal_weight__momentum_trend+volatility_risk").length).toBeGreaterThan(0);
  expect(screen.getByText("Controlled dimension ablation")).toBeInTheDocument();
  expect(screen.getByText("Model redundancy alerts")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("strategy page separates rules, products, and target decisions without performance", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/strategies?lang=en");
  expect(await screen.findByRole("heading", { name: "Strategy rules and target weights" })).toBeInTheDocument();
  expect(screen.getAllByText("top_k_equal_weight__k2").length).toBeGreaterThan(0);
  expect(await screen.findByText("selected_by_rank")).toBeInTheDocument();
  expect(screen.getByText("50%")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe ratio/i)).not.toBeInTheDocument();
});

test("experiment page joins comparable cells, performance, and run audit", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/experiments?lang=en");
  expect(await screen.findByRole("heading", { name: "Strategy experiments and traceable performance" })).toBeInTheDocument();
  expect(screen.getByText("12%")).toBeInTheDocument();
  expect(screen.getByText("9%")).toBeInTheDocument();
  expect(await screen.findByText("All outputs published")).toBeInTheDocument();
  expect(screen.getByText("Complete performance metrics")).toBeInTheDocument();
  expect(screen.getByText("Strategy Product Ranking")).toBeInTheDocument();
  expect(screen.getAllByText("1.1").length).toBeGreaterThan(0);
  expect(await screen.findByRole("heading", { name: "Decision Explorer" })).toBeInTheDocument();
  expect(screen.getByText("total_return__w252")).toBeInTheDocument();
});

test("compare page labels a one-dimension change as controlled", async () => {
  await i18n.changeLanguage("en");
  const second = { ...experimentOverview.specifications[0],
    artifact_id: "00000000-0000-0000-0000-000000000081",
    result_artifact_id: "00000000-0000-0000-0000-000000000081", variant_key: "top_k_equal_weight__k3" };
  experimentOverview.specifications.push(second);
  renderRoute("/compare?lang=en");
  expect(await screen.findByRole("heading", { name: "Strategy Product Compare" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Controlled comparison" })).toBeInTheDocument();
  expect(screen.getByText(/Only k changed/)).toBeInTheDocument();
  experimentOverview.specifications.pop();
});
